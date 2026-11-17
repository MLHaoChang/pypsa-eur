"""
Issue 06 slice (a): the optional pinned-Blender worker (owner decision Q-SS-2).

Blender-free GLB plus manifest (`export.py`) stays the default. This module
runs the pinned Blender LTS in a container whose whole command line it builds
itself. It never imports `bpy` and never opens a `.blend` it did not write.

The operator configures three things, and nothing else:
- `PYPSA_GUI_BLENDER_DIR`: the unpacked `blender-5.2.4-linux-x64` tree. Its
  whole-tree manifest sha256 must equal `TREE_SHA256` (the executable, the
  bundled Python and the startup scripts alike).
- `PYPSA_GUI_BLENDER_RUNTIME`: an absolute path to `docker` or `podman`.
- `PYPSA_GUI_BLENDER_IMAGE`: a runtime image pinned by digest
  (`name@sha256:…`) holding the shared libraries Blender needs. The image's
  own files never run as Blender: the verified tree is bind-mounted read-only
  at `/opt/blender`.

The container (`_container_argv`) always gets:
- no network, a read-only root, all capabilities dropped, no new privileges,
  a non-root user and an init process;
- memory (no swap), pids, CPU-time, file-size, open-file and core limits;
- exactly two mounts: the 0700 work directory at `/work` and the tree at
  `/opt/blender`, read-only.

The process runs Blender with `--background --factory-startup --offline-mode
--python-exit-code 3` and a trusted script (`SCRIPT`), and gets an
allowlisted environment.

Cancel and the wall timeout run `<runtime> kill <name>`, then kill the CLI's
process group. Exit statuses map to typed kinds: 137 is the memory cap (or a
kill from outside), 152 is SIGXCPU, and 153 is SIGXFSZ. Outputs are read
through `O_NOFOLLOW`, regular files only, under a size cap.

The caps are confirmed against the SC06-CI-1 measured run (GK B, B5; run
44013964974 on 6b559a5e): a 3-node round trip took 2.8 s wall, 2.7 s CPU and
330 MiB peak RSS, and wrote 0.5 MiB. The caps leave room for larger scenes and
container start-up. They are stated in `Caps` and in issue 06.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PIN_VERSION = "5.2.4"
# blender.org's blender-5.2.4.sha256 for the release tarball.
TARBALL_SHA256 = "2fd14c50987426897ba60237ee996edf2b4151f4b1a72a460ca20be58c1d2d86"
# `tree_sha256` of the tree unpacked from that verified tarball.
TREE_SHA256 = "9c3072a9c1b849f095e66c4f02960d0ab3ed4655d015cabf4e41aa51839ba3be"

ENV_DIR = "PYPSA_GUI_BLENDER_DIR"
ENV_RUNTIME = "PYPSA_GUI_BLENDER_RUNTIME"
ENV_IMAGE = "PYPSA_GUI_BLENDER_IMAGE"
RUNTIMES = ("docker", "podman")
_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/:-]{0,200}@sha256:[0-9a-f]{64}$")

# The trusted script, run inside Blender only. Kept as text so the backend
# never imports `bpy`. It imports the native export GLB with its node extras
# (feature_id, kind, source_class, bindings), saves an uncompressed `.blend`,
# and exports a Y-up GLB with the extras kept. It opens no other file. An
# exception exits 3 (`--python-exit-code 3`).
SCRIPT = """\
import sys
import bpy
src, out_glb, out_blend = sys.argv[sys.argv.index("--") + 1:]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src, import_scene_extras=True)
bpy.ops.wm.save_as_mainfile(filepath=out_blend, compress=False)
bpy.ops.export_scene.gltf(filepath=out_glb, export_format="GLB", export_extras=True, export_yup=True)
print("DONE", len(bpy.data.objects), "objects")
"""

# Job conditions (the job's `error` prefix), not tool error kinds.
UNAVAILABLE = "blender_unavailable"
TIMEOUT = "blender_timeout"
CPU_LIMIT = "blender_cpu_limit"
MEMORY_LIMIT = "blender_memory_limit"
OUTPUT_LIMIT = "blender_output_limit"
FAILED = "blender_failed"
CANCELLED = "blender_cancelled"
KINDS = (UNAVAILABLE, TIMEOUT, CPU_LIMIT, MEMORY_LIMIT, OUTPUT_LIMIT, FAILED, CANCELLED)

# The runtime CLI sees only these names from the parent, when set.
ENV_ALLOW = ("DOCKER_HOST", "XDG_RUNTIME_DIR")


class WorkerError(Exception):
    """A refused or failed worker run. `kind` is one of `KINDS`; the message names no host path."""

    def __init__(self, kind: str, message: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"unclassified worker kind: {kind}")
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class Caps:
    """Confirmed against the SC06-CI-1 run: about 45x its CPU, 6x its RSS, 65x its wall time."""

    wall_s: float = 180.0
    cpu_s: int = 120
    memory_bytes: int = 2 << 30
    fsize_bytes: int = 512 << 20
    output_bytes: int = 256 << 20
    nofile: int = 256
    pids: int = 256
    grace_s: float = 5.0
    version_timeout_s: float = 30.0
    poll_s: float = 0.1
    ready_ttl_s: float = 60.0


@dataclass(frozen=True)
class Readiness:
    available: bool
    reason: str | None
    blender_dir: str | None = None
    runtime: str | None = None
    image: str | None = None


# ── the pinned tree ──────────────────────────────────────────────────────────


def _walk(root: Path):
    """Every regular file and symlink under `root`, sorted, never following links."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        links = [d for d in dirnames if Path(dirpath, d).is_symlink()]
        for name in sorted(filenames + links):
            yield Path(dirpath, name)


def tree_sha256(root: Path) -> str:
    """One sha256 over every file's relative path and content (and every link's target)."""
    h = hashlib.sha256()
    for p in _walk(root):
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            h.update(f"L\0{rel}\0{os.readlink(p)}\n".encode())
        elif p.is_file():
            f = hashlib.sha256()
            with p.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    f.update(chunk)
            h.update(f"F\0{rel}\0{f.hexdigest()}\n".encode())
    return h.hexdigest()


def _stat_key(root: Path) -> str:
    h = hashlib.sha256()
    for p in _walk(root):
        st = p.lstat()
        h.update(
            f"{p.relative_to(root).as_posix()}\0{st.st_size}\0{st.st_mtime_ns}\0{st.st_mode}\n".encode()
        )
    return h.hexdigest()


_tree_cache: dict[str, tuple[str, str]] = {}
_ready_cache: dict[tuple, tuple[float, Readiness]] = {}
_cache_lock = threading.Lock()


def _cached_tree_sha256(root: Path) -> str:
    """The content hash is recomputed only when a file's size, mtime or mode changes."""
    key = _stat_key(root)
    with _cache_lock:
        hit = _tree_cache.get(str(root))
        if hit and hit[0] == key:
            return hit[1]
    digest = tree_sha256(root)
    with _cache_lock:
        _tree_cache[str(root)] = (key, digest)
    return digest


# ── readiness ────────────────────────────────────────────────────────────────


def _config(env) -> tuple[Path, str, str]:
    raw_dir, runtime, image = env.get(ENV_DIR), env.get(ENV_RUNTIME), env.get(ENV_IMAGE)
    if not raw_dir or not runtime or not image:
        missing = [
            n
            for n, v in ((ENV_DIR, raw_dir), (ENV_RUNTIME, runtime), (ENV_IMAGE, image))
            if not v
        ]
        raise WorkerError(
            UNAVAILABLE,
            f"{', '.join(missing)} not set; Blender runs only in a container",
        )
    root = Path(raw_dir)
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise WorkerError(
            UNAVAILABLE,
            f"{ENV_DIR} is not an absolute path to the unpacked Blender directory",
        )
    rt = Path(runtime)
    if (
        not rt.is_absolute()
        or rt.name not in RUNTIMES
        or not rt.is_file()
        or not os.access(rt, os.X_OK)
    ):
        raise WorkerError(
            UNAVAILABLE, f"{ENV_RUNTIME} is not an absolute path to docker or podman"
        )
    if not _IMAGE.fullmatch(image):
        raise WorkerError(
            UNAVAILABLE, f"{ENV_IMAGE} must be pinned by digest (name@sha256:...)"
        )
    return root, str(rt), image


def readiness(env: dict[str, str] | None = None, caps: Caps = Caps()) -> Readiness:
    """Whether a worker run can start, with the reason when it cannot. Never raises."""
    env = os.environ if env is None else env
    key = (env.get(ENV_DIR), env.get(ENV_RUNTIME), env.get(ENV_IMAGE), caps)
    with _cache_lock:
        hit = _ready_cache.get(key)
        if hit and time.monotonic() - hit[0] < caps.ready_ttl_s:
            return hit[1]
    try:
        root, runtime, image = _config(env)
        if not (root / "blender").is_file():
            raise WorkerError(UNAVAILABLE, f"{ENV_DIR} has no blender executable")
        if _cached_tree_sha256(root) != TREE_SHA256:
            raise WorkerError(
                UNAVAILABLE,
                f"the Blender directory does not match the {PIN_VERSION} pin (sha256)",
            )
        _check_image(runtime, image, env, caps)
        ready = Readiness(True, None, str(root), runtime, image)
        _check_version(ready, env, caps)
    except WorkerError as exc:
        ready = Readiness(False, exc.message)
    except (OSError, subprocess.SubprocessError) as exc:
        ready = Readiness(False, f"Blender could not be checked ({type(exc).__name__})")
    with _cache_lock:
        _ready_cache[key] = (time.monotonic(), ready)
    return ready


def _check_image(runtime: str, image: str, env, caps: Caps) -> None:
    """The pinned image must already be local: a run never pulls (`--pull never`)."""
    try:
        rc = subprocess.run(
            [runtime, "image", "inspect", image],
            env=child_env(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=caps.version_timeout_s,
            check=False,
        ).returncode
    except subprocess.TimeoutExpired:
        raise WorkerError(UNAVAILABLE, "the container runtime did not answer") from None
    if rc != 0:
        raise WorkerError(
            UNAVAILABLE, "the pinned image is not present locally; pull it first"
        )


def _check_version(ready: Readiness, env, caps: Caps) -> None:
    work = _workdir()
    try:
        out = _run(
            ready,
            work,
            ["--version"],
            env,
            caps,
            wall_s=caps.version_timeout_s,
            stop_event=None,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    lines = out.decode("utf-8", "replace").strip().splitlines()
    first = lines[0] if lines else ""
    if first != f"Blender {PIN_VERSION}" and not first.startswith(
        f"Blender {PIN_VERSION} "
    ):
        raise WorkerError(
            UNAVAILABLE, f"the binary does not report Blender {PIN_VERSION}"
        )


# ── the container ────────────────────────────────────────────────────────────


def _user() -> str:
    uid, gid = os.getuid(), os.getgid()
    return f"{uid}:{gid}" if uid != 0 else "65534:65534"


def _container_argv(
    ready: Readiness, name: str, work: Path, tail: list[str], caps: Caps
) -> list[str]:
    """The whole command line. Nothing in it comes from a request or from operator free text:
    `name` is random per run, `work` comes from mkdtemp, and the tree, runtime and image are
    the validated settings."""
    return [
        ready.runtime,
        "run",
        "--rm",
        "--pull",
        "never",
        "--name",
        name,
        "--init",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        _user(),
        "--memory",
        str(caps.memory_bytes),
        "--memory-swap",
        str(caps.memory_bytes),
        "--pids-limit",
        str(caps.pids),
        "--ulimit",
        f"cpu={caps.cpu_s}:{caps.cpu_s + 5}",  # SIGXCPU at the soft limit, SIGKILL 5 s later
        "--ulimit",
        f"fsize={caps.fsize_bytes}:{caps.fsize_bytes}",
        "--ulimit",
        f"nofile={caps.nofile}:{caps.nofile}",
        "--ulimit",
        "core=0:0",
        "--tmpfs",
        "/tmp:size=64m",
        "--mount",
        f"type=bind,source={work},target=/work",
        "--mount",
        f"type=bind,source={ready.blender_dir},target=/opt/blender,readonly",
        "--workdir",
        "/work",
        "--env",
        "HOME=/work",
        "--env",
        "TMPDIR=/tmp",
        "--env",
        "LANG=C.UTF-8",
        ready.image,
        "/opt/blender/blender",
        "--background",
        "--factory-startup",
        "--offline-mode",
        *tail,
    ]


def child_env(env) -> dict[str, str]:
    """The runtime CLI's whole environment: an allowlist, never the parent's."""
    out = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    for name in ENV_ALLOW:
        if env.get(name):
            out[name] = env[name]
    return out


def _cli_limits() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _killpg(pgid: int, grace_s: float) -> None:
    """SIGTERM the CLI's group, wait up to `grace_s` for it to empty, then SIGKILL."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _kill_container(ready: Readiness, name: str, env, grace_s: float) -> None:
    try:
        subprocess.run(
            [ready.runtime, "kill", name],
            env=child_env(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=grace_s + 10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _workdir() -> Path:
    work = Path(tempfile.mkdtemp(prefix="blender-run-"))
    if os.getuid() == 0:
        os.chown(work, 65534, 65534)
    os.chmod(work, 0o700)
    return work


def _run(
    ready: Readiness,
    work: Path,
    tail: list[str],
    env,
    caps: Caps,
    *,
    wall_s: float,
    stop_event,
) -> bytes:
    """One container run. Returns the tail of its combined output."""
    name = f"pypsa-blender-{uuid.uuid4().hex}"
    log_fd, log_path = tempfile.mkstemp(prefix="blender-log-")
    try:
        proc = subprocess.Popen(
            _container_argv(ready, name, work, tail, caps),
            cwd=work,
            env=child_env(env),
            stdin=subprocess.DEVNULL,
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
            preexec_fn=_cli_limits,
        )
        failure: WorkerError | None = None
        deadline = time.monotonic() + wall_s
        try:
            while proc.poll() is None:
                if stop_event is not None and stop_event.is_set():
                    failure = WorkerError(CANCELLED, "the run was cancelled")
                elif time.monotonic() > deadline:
                    failure = WorkerError(TIMEOUT, f"the run exceeded {wall_s:g} s")
                if failure is not None:
                    break
                time.sleep(caps.poll_s)
        finally:
            # Every path, success included: the container and the CLI's group end here.
            if proc.poll() is None:
                _kill_container(ready, name, env, caps.grace_s)
            _killpg(proc.pid, caps.grace_s)
            try:
                proc.wait(timeout=caps.grace_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if failure is not None:
            raise failure
        size = os.fstat(log_fd).st_size
        os.lseek(log_fd, max(0, size - 64 * 1024), os.SEEK_SET)
        tail_bytes = os.read(log_fd, 64 * 1024)
    finally:
        os.close(log_fd)
        os.unlink(log_path)
    rc = proc.returncode
    if rc == 128 + signal.SIGXCPU:
        raise WorkerError(CPU_LIMIT, f"the run exceeded {caps.cpu_s} s of CPU")
    if rc == 128 + signal.SIGXFSZ:
        raise WorkerError(
            OUTPUT_LIMIT, f"the run wrote a file over {caps.fsize_bytes >> 20} MiB"
        )
    if rc == 128 + signal.SIGKILL:
        raise WorkerError(
            MEMORY_LIMIT,
            f"the run was killed: over {caps.memory_bytes >> 20} MiB, or killed outside the app",
        )
    if rc != 0:
        raise WorkerError(FAILED, f"Blender exited with status {rc}")
    return tail_bytes


@dataclass(frozen=True)
class Result:
    glb: bytes
    blend: bytes
    log_tail: str


def _read_output(work: Path, name: str, cap: int) -> bytes:
    """A regular file directly in `work`, never through a symlink, at most `cap` bytes."""
    try:
        fd = os.open(
            work / name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
        )
    except FileNotFoundError:
        raise WorkerError(FAILED, f"Blender wrote no {name}") from None
    except OSError:
        raise WorkerError(FAILED, f"{name} is not a regular file") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise WorkerError(FAILED, f"{name} is not a regular file")
        if st.st_size > cap:
            raise WorkerError(OUTPUT_LIMIT, f"{name} is over {cap >> 20} MiB")
        chunks, total = [], 0
        while chunk := os.read(fd, 1 << 20):
            total += len(chunk)
            if total > cap:
                raise WorkerError(OUTPUT_LIMIT, f"{name} is over {cap >> 20} MiB")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def run(
    ready: Readiness,
    glb: bytes,
    stop_event,
    caps: Caps = Caps(),
    env: dict[str, str] | None = None,
) -> Result:
    """One worker run on the native export `glb`. The work directory never outlives it."""
    if not ready.available:
        raise WorkerError(UNAVAILABLE, ready.reason or "Blender is not available")
    env = os.environ if env is None else env
    work = _workdir()
    try:
        (work / "in.glb").write_bytes(glb)
        (work / "run.py").write_text(SCRIPT, encoding="utf-8")
        tail = _run(
            ready,
            work,
            [
                "--python-exit-code",
                "3",
                "--python",
                "/work/run.py",
                "--",
                "/work/in.glb",
                "/work/out.glb",
                "/work/out.blend",
            ],
            env,
            caps,
            wall_s=caps.wall_s,
            stop_event=stop_event,
        )
        return Result(
            _read_output(work, "out.glb", caps.output_bytes),
            _read_output(work, "out.blend", caps.output_bytes),
            tail.decode("utf-8", "replace"),
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def describe(ready: Readiness) -> dict[str, Any]:
    return {
        "available": ready.available,
        "reason": ready.reason,
        "version": PIN_VERSION,
    }
