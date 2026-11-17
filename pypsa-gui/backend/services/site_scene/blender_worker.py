"""
Issue 06 slice (a): the optional pinned-Blender worker (owner decision Q-SS-2).

Blender-free GLB plus manifest (`export.py`) stays the default. This module
runs the pinned Blender LTS binary as one sandboxed child process tree. It
never imports `bpy` and never opens a `.blend` it did not write itself.

What the child gets:
- Its input is the native export GLB of one pointed generation, plus a trusted
  script kept here as text (`SCRIPT`). Both are copied into a
  fresh 0700 work directory.
- It always runs through an operator-configured container wrapper (GK B, Q1).
  With no wrapper, or a wrapper missing `--network none`, `--read-only`,
  `--user` or `--memory`, the capability reads unavailable. There is no
  in-host mode.
- Its environment is an allowlist (nothing inherited: no secrets, no
  PYTHON*, no LD_*).
- It runs under the flags `--background --factory-startup --offline-mode
  --python-exit-code`.
- It is bounded: rlimits on the spawned process (CPU, file size, open files,
  no core), a process-group RSS ceiling polled here, a wall timeout, and an
  output size cap. Cancel and every limit kill the whole process group
  (SIGTERM, grace, SIGKILL).

Readiness checks the binary's sha256 against the pin and runs `--version`
through the same sandbox, with a timeout.

The caps are conservative placeholders until the SC06-CI-1 measured run lands
(GK B, B5). They are stated in `Caps` and in the PR.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PIN_VERSION = "5.2.4"
PIN_SHA256 = "2fd14c50987426897ba60237ee996edf2b4151f4b1a72a460ca20be58c1d2d86"
# PIN_SHA256 is the release tarball's (blender.org's blender-5.2.4.sha256).
# BINARY_SHA256 is the `blender` executable unpacked from that verified tarball.
BINARY_SHA256 = "9c12ad1b09401dcd3a8a5336279bca91455f15023aa0e5e4cb5cdb7b116a09f1"

ENV_BINARY = "PYPSA_GUI_BLENDER"
ENV_WRAPPER = "PYPSA_GUI_BLENDER_WRAPPER"

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

# The child sees only these names, and only when the parent has them set.
ENV_ALLOW = ("DOCKER_HOST",)


class WorkerError(Exception):
    """A refused or failed worker run. `kind` is one of `KINDS`."""

    def __init__(self, kind: str, message: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"unclassified worker kind {kind!r}")
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class Caps:
    """Conservative until the SC06-CI-1 numbers land."""

    wall_s: float = 180.0
    cpu_s: int = 120
    rss_bytes: int = 2 << 30
    fsize_bytes: int = 512 << 20
    output_bytes: int = 256 << 20
    nofile: int = 256
    grace_s: float = 5.0
    version_timeout_s: float = 30.0
    poll_s: float = 0.1


@dataclass(frozen=True)
class Readiness:
    available: bool
    reason: str | None
    binary: str | None = None
    wrapper: tuple[str, ...] = ()


# ── readiness ────────────────────────────────────────────────────────────────


def _flag(argv: list[str], name: str, value: str | None = None) -> bool:
    """`name value`, `name=value`, or a bare `name` when `value` is None."""
    for i, a in enumerate(argv):
        if value is None:
            if a == name or a.startswith(name + "="):
                return True
        elif a == f"{name}={value}" or (
            a == name and i + 1 < len(argv) and argv[i + 1] == value
        ):
            return True
    return False


def parse_wrapper(raw: str | None) -> tuple[str, ...]:
    """The wrapper argv, or `WorkerError(UNAVAILABLE)` naming what is missing."""
    if not raw:
        raise WorkerError(
            UNAVAILABLE, f"{ENV_WRAPPER} is not set; Blender runs only in a container"
        )
    try:
        argv = json.loads(raw)
    except ValueError:
        raise WorkerError(UNAVAILABLE, f"{ENV_WRAPPER} is not a JSON list") from None
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(a, str) and a for a in argv)
    ):
        raise WorkerError(
            UNAVAILABLE, f"{ENV_WRAPPER} is not a non-empty list of strings"
        )
    if argv.count("{blender}") != 1:
        raise WorkerError(
            UNAVAILABLE, f"{ENV_WRAPPER} must name {{blender}} exactly once"
        )
    missing = [
        label
        for label, ok in (
            ("--network none", _flag(argv, "--network", "none")),
            ("--read-only", _flag(argv, "--read-only")),
            ("--user", _flag(argv, "--user")),
            ("--memory", _flag(argv, "--memory")),
        )
        if not ok
    ]
    if missing:
        raise WorkerError(UNAVAILABLE, f"{ENV_WRAPPER} lacks {', '.join(missing)}")
    return tuple(argv)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_hash_cache: dict[tuple[str, int, int], str] = {}
_hash_lock = threading.Lock()


def _binary_sha256(path: Path) -> str:
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _hash_lock:
        if key not in _hash_cache:
            _hash_cache.clear()
            _hash_cache[key] = _sha256(path)
        return _hash_cache[key]


def readiness(env: dict[str, str] | None = None, caps: Caps = Caps()) -> Readiness:
    """Whether a worker run can start, with the reason when it cannot. Never raises."""
    env = os.environ if env is None else env
    try:
        wrapper = parse_wrapper(env.get(ENV_WRAPPER))
        raw = env.get(ENV_BINARY)
        if not raw:
            raise WorkerError(UNAVAILABLE, f"{ENV_BINARY} is not set")
        binary = Path(raw)
        if (
            not binary.is_absolute()
            or not binary.is_file()
            or not os.access(binary, os.X_OK)
        ):
            raise WorkerError(
                UNAVAILABLE,
                f"{ENV_BINARY} is not an absolute path to an executable file",
            )
        if _binary_sha256(binary) != BINARY_SHA256:
            raise WorkerError(
                UNAVAILABLE,
                f"the binary's sha256 does not match the Blender {PIN_VERSION} pin",
            )
        _check_version(str(binary), wrapper, env, caps)
    except WorkerError as exc:
        return Readiness(False, exc.message)
    except OSError as exc:
        return Readiness(False, f"the binary cannot be read: {exc.strerror or exc}")
    return Readiness(True, None, str(binary), wrapper)


def _check_version(binary: str, wrapper: tuple[str, ...], env, caps: Caps) -> None:
    work = Path(tempfile.mkdtemp(prefix="blender-ready-"))
    try:
        out = _run(
            _argv(wrapper, binary, work, ["--version"]),
            work,
            env,
            caps,
            wall_s=caps.version_timeout_s,
            stop_event=None,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    first = out.decode("utf-8", "replace").strip().splitlines()[:1]
    if first != [f"Blender {PIN_VERSION}"] and not (
        first and first[0].startswith(f"Blender {PIN_VERSION} ")
    ):
        raise WorkerError(
            UNAVAILABLE, f"the binary does not report Blender {PIN_VERSION}"
        )


# ── the sandboxed child ──────────────────────────────────────────────────────


def _argv(
    wrapper: tuple[str, ...], binary: str, work: Path, tail: list[str]
) -> list[str]:
    head = [
        a.replace("{workdir}", str(work)).replace(
            "{blender_dir}", str(Path(binary).parent)
        )
        for a in wrapper
    ]
    i = head.index("{blender}")
    return [
        *head[:i],
        binary,
        "--background",
        "--factory-startup",
        "--offline-mode",
        *tail,
    ]


def child_env(env, work: Path) -> dict[str, str]:
    """The child's whole environment: an allowlist, never the parent's."""
    out = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(work),
        "TMPDIR": str(work),
        "LANG": "C.UTF-8",
    }
    for name in ENV_ALLOW:
        if env.get(name):
            out[name] = env[name]
    return out


def _limits(caps: Caps):
    def apply() -> None:
        import resource

        # CPython ignores SIGXFSZ and SIGPIPE, and an ignored disposition
        # survives exec: restore the defaults, so a file cap kills the child
        # with a signal this module can type.
        signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (caps.cpu_s, caps.cpu_s + 5))
        resource.setrlimit(resource.RLIMIT_FSIZE, (caps.fsize_bytes, caps.fsize_bytes))
        resource.setrlimit(resource.RLIMIT_NOFILE, (caps.nofile, caps.nofile))

    return apply


def _group_rss(pgid: int) -> int:
    """Resident bytes of every live process in group `pgid` (Linux /proc)."""
    page = os.sysconf("SC_PAGE_SIZE")
    total = 0
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            stat = Path(entry.path, "stat").read_text()
            fields = stat[stat.rindex(")") + 2 :].split()
            if int(fields[2]) != pgid:
                continue
            total += int(Path(entry.path, "statm").read_text().split()[1]) * page
        except (OSError, ValueError, IndexError):
            continue
    return total


def _killpg(pgid: int, grace_s: float) -> None:
    """SIGTERM the group, wait up to `grace_s` for it to empty, then SIGKILL."""
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


def _run(
    argv: list[str], work: Path, env, caps: Caps, *, wall_s: float, stop_event
) -> bytes:
    """Run `argv` in `work`, bounded. Returns its combined output (tail-capped)."""
    log = work / "worker.log"
    with log.open("wb") as out:
        proc = subprocess.Popen(
            argv,
            cwd=work,
            env=child_env(env, work),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
            preexec_fn=_limits(caps),
        )
    pgid = proc.pid
    deadline = time.monotonic() + wall_s
    failure: WorkerError | None = None
    try:
        while proc.poll() is None:
            if stop_event is not None and stop_event.is_set():
                failure = WorkerError(CANCELLED, "the run was cancelled")
            elif time.monotonic() > deadline:
                failure = WorkerError(TIMEOUT, f"the run exceeded {wall_s:g} s")
            elif _group_rss(pgid) > caps.rss_bytes:
                failure = WorkerError(
                    MEMORY_LIMIT,
                    f"the run exceeded {caps.rss_bytes >> 20} MiB resident",
                )
            if failure is not None:
                break
            time.sleep(caps.poll_s)
    finally:
        # Every path, success included: nothing in the group outlives the run.
        _killpg(pgid, caps.grace_s)
        try:
            proc.wait(timeout=caps.grace_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    if failure is not None:
        raise failure
    with log.open("rb") as f:
        f.seek(max(0, log.stat().st_size - 64 * 1024))
        tail = f.read()
    rc = proc.returncode
    if rc == -signal.SIGXCPU:
        raise WorkerError(CPU_LIMIT, f"the run exceeded {caps.cpu_s} s of CPU")
    if rc == -signal.SIGXFSZ:
        raise WorkerError(
            OUTPUT_LIMIT, f"the run wrote a file over {caps.fsize_bytes >> 20} MiB"
        )
    if rc != 0:
        raise WorkerError(FAILED, f"Blender exited with status {rc}")
    return tail


@dataclass(frozen=True)
class Result:
    glb: bytes
    blend: bytes
    log_tail: str


def run(
    ready: Readiness,
    glb: bytes,
    stop_event,
    caps: Caps = Caps(),
    env: dict[str, str] | None = None,
) -> Result:
    """One worker run on the native export `glb`. The work directory never outlives it."""
    if not ready.available or ready.binary is None:
        raise WorkerError(UNAVAILABLE, ready.reason or "Blender is not available")
    env = os.environ if env is None else env
    work = Path(tempfile.mkdtemp(prefix="blender-run-"))
    try:
        os.chmod(work, 0o700)
        (work / "in.glb").write_bytes(glb)
        (work / "run.py").write_text(SCRIPT, encoding="utf-8")
        tail = _run(
            _argv(
                ready.wrapper,
                ready.binary,
                work,
                [
                    "--python-exit-code",
                    "3",
                    "--python",
                    str(work / "run.py"),
                    "--",
                    str(work / "in.glb"),
                    str(work / "out.glb"),
                    str(work / "out.blend"),
                ],
            ),
            work,
            env,
            caps,
            wall_s=caps.wall_s,
            stop_event=stop_event,
        )
        outputs = {}
        for name in ("out.glb", "out.blend"):
            p = work / name
            if not p.is_file() or p.is_symlink():
                raise WorkerError(FAILED, f"Blender wrote no {name}")
            if p.stat().st_size > caps.output_bytes:
                raise WorkerError(
                    OUTPUT_LIMIT, f"{name} is over {caps.output_bytes >> 20} MiB"
                )
            outputs[name] = p.read_bytes()
        return Result(
            outputs["out.glb"], outputs["out.blend"], tail.decode("utf-8", "replace")
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def describe(ready: Readiness) -> dict[str, Any]:
    return {
        "available": ready.available,
        "reason": ready.reason,
        "version": PIN_VERSION,
    }
