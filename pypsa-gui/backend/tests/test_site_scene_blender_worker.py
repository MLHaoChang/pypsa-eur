"""Issue 06 slice (a): the containerised Blender worker, with a fake runtime and a fake Blender.

CI has neither docker nor Blender. The fake `docker`:
- checks the isolation flags the worker must always pass;
- maps the two bind mounts;
- applies the CPU and file-size ulimits;
- starts the "container" in its OWN session, as containerd would, so killing
  the CLI's process group does not stop it;
- enforces `--memory` by SIGKILL (exit 137), and exits 128+signal as `--init`
  does;
- answers `docker kill <name>` from a pid registry.

The fake Blender (`<tree>/blender`) honours `--version` and `--python run.py
-- IN OUT.glb OUT.blend` in one mode per test.

GK B's conditions here are B2 (readiness pin), B3 (env allowlist), B4 (the
container dies on cancel and timeout; the temp dir is removed) and B5 (each
cap refused with a typed kind).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from services.site_scene import blender_worker as bw

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="the worker is Linux-only"
)

IMAGE = "registry.example/blender-runtime@sha256:" + "a" * 64

RUNTIME = """\
#!{python}
import json, os, resource, signal, subprocess, sys, time
STATE = {state!r}
REC = {rec!r}
NOIMG = {noimg!r}
a = sys.argv[1:]
if a[:2] == ["image", "inspect"]:
    sys.exit(1 if NOIMG else 0)
if a[0] == "kill":
    try:
        pid = int(open(os.path.join(STATE, a[1])).read())
        os.killpg(pid, signal.SIGKILL)
    except (OSError, ValueError):
        sys.exit(1)
    sys.exit(0)
assert a[0] == "run", a
with open(REC + ".cli", "w") as f:
    json.dump({{"env": dict(os.environ), "argv": a}}, f)
assert a[a.index("--pull") + 1] == "never", a
need = ["--rm", "--init", "--read-only", "--name", "--network", "--cap-drop", "--security-opt", "--user",
        "--memory", "--memory-swap", "--pids-limit", "--ulimit", "--mount", "--tmpfs"]
assert all(n in a for n in need), a
opts, i = {{}}, 1
while not a[i].startswith("registry."):
    if a[i] in ("--rm", "--init", "--read-only"):
        i += 1
        continue
    opts.setdefault(a[i], []).append(a[i + 1])
    i += 2
image, cmd = a[i], a[i + 1:]
assert opts["--network"] == ["none"] and opts["--cap-drop"] == ["ALL"], opts
mounts = {{}}
for m in opts["--mount"]:
    kv = dict(p.split("=", 1) for p in m.split(",") if "=" in p)
    mounts[kv["target"]] = kv["source"]
assert set(mounts) == {{"/work", "/opt/blender"}} and opts["--mount"][1].endswith(",readonly"), mounts
lim = {{u.split("=")[0]: tuple(int(x) for x in u.split("=")[1].split(":")) for u in opts["--ulimit"]}}
mem = int(opts["--memory"][0])

def host(p):
    for t, s in mounts.items():
        if p == t or p.startswith(t + "/"):
            return s + p[len(t):]
    return p

def limits():
    resource.setrlimit(resource.RLIMIT_CPU, lim["cpu"])
    resource.setrlimit(resource.RLIMIT_FSIZE, lim["fsize"])
    signal.signal(signal.SIGXFSZ, signal.SIG_DFL)

kid = subprocess.Popen([host(c) for c in cmd], cwd=mounts["/work"], start_new_session=True,
                       preexec_fn=limits, env={{"HOME": mounts["/work"], "FAKE_REC": REC}})
open(os.path.join(STATE, opts["--name"][0]), "w").write(str(kid.pid))

def rss(pgid):
    total = 0
    for e in os.listdir("/proc"):
        if e.isdigit():
            try:
                st = open(f"/proc/{{e}}/stat").read()
                if int(st[st.rindex(")") + 2:].split()[2]) == pgid:
                    total += int(open(f"/proc/{{e}}/statm").read().split()[1]) * 4096
            except (OSError, ValueError):
                pass
    return total

while kid.poll() is None:
    if rss(kid.pid) > mem:
        os.killpg(kid.pid, signal.SIGKILL)
    time.sleep(0.05)
try:
    os.killpg(kid.pid, signal.SIGKILL)   # container teardown
except ProcessLookupError:
    pass
rc = kid.returncode
sys.exit(128 - rc if rc < 0 else rc)
"""

STUB = """\
#!{python}
import json, os, signal, subprocess, sys, time
MODE = {mode!r}
REC = os.environ["FAKE_REC"]
args = sys.argv[1:]
if "--version" in args:
    if MODE == "slow_version":
        time.sleep(60)
    print("Blender 9.9.9" if MODE == "wrong_version" else "Blender 5.2.4 LTS")
    sys.exit(0)
src, out_glb, out_blend = args[args.index("--") + 1:]
with open(REC, "w") as f:
    json.dump({{"env": dict(os.environ), "cwd": os.getcwd(), "pid": os.getpid(), "args": args}}, f)
if MODE == "hang":
    kid = subprocess.Popen([sys.executable, "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)"])
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    with open(REC + ".kid", "w") as f:
        f.write(str(kid.pid))
    time.sleep(600)
if MODE == "cpu":
    while True:
        pass
if MODE == "fsize":
    signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
    with open(out_blend, "wb") as f:
        while True:
            f.write(b"x" * 65536)
if MODE == "memory":
    hog = bytearray(400 << 20)
    for i in range(0, len(hog), 4096):
        hog[i] = 1
    time.sleep(600)
if MODE == "crash":
    sys.exit(3)
if MODE == "no_output":
    sys.exit(0)
data = open(src, "rb").read()
open(out_glb, "wb").write(data * (100 if MODE == "big" else 1))
if MODE == "symlink":
    os.symlink("/etc/hostname", out_blend)
else:
    open(out_blend, "wb").write(b"BLENDER-v502")
print("DONE stub")
"""


def _alive(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ")[1][0] != "Z"
    except (OSError, IndexError):
        return False


@pytest.fixture(autouse=True)
def _fresh_caches():
    bw._ready_cache.clear()
    bw._tree_cache.clear()
    yield
    bw._ready_cache.clear()


@pytest.fixture
def make(tmp_path, monkeypatch):
    """`make(mode)` -> (env, record path): a fake runtime plus a pinned fake Blender tree in `mode`."""

    def build(mode: str = "ok"):
        d = tmp_path / mode
        tree, state, bin_dir = d / "blender-5.2.4-linux-x64", d / "state", d / "bin"
        for p in (tree / "5.2" / "scripts", state, bin_dir):
            p.mkdir(parents=True)
        (tree / "5.2" / "scripts" / "startup.py").write_text("# bundled\n")
        rec = d / "record.json"
        stub = tree / "blender"
        stub.write_text(STUB.format(python=sys.executable, mode=mode))
        stub.chmod(0o755)
        runtime = bin_dir / "docker"
        runtime.write_text(
            RUNTIME.format(
                python=sys.executable,
                state=str(state),
                rec=str(rec),
                noimg=mode == "no_image",
            )
        )
        runtime.chmod(0o755)
        monkeypatch.setattr(bw, "TREE_SHA256", bw.tree_sha256(tree))
        env = {bw.ENV_DIR: str(tree), bw.ENV_RUNTIME: str(runtime), bw.ENV_IMAGE: IMAGE}
        return env, rec

    build.state = lambda mode="ok": tmp_path / mode / "state"
    return build


FAST = bw.Caps(
    wall_s=20,
    cpu_s=2,
    memory_bytes=200 << 20,
    fsize_bytes=1 << 20,
    output_bytes=1 << 20,
    grace_s=1.0,
    version_timeout_s=3.0,
    poll_s=0.05,
)


def _caps(**over):
    return bw.Caps(**{**FAST.__dict__, **over})


def _ready(env, caps=FAST):
    r = bw.readiness(env, caps)
    assert r.available, r.reason
    return r


# ── B2 readiness ─────────────────────────────────────────────────────────────


def test_a_pinned_tree_with_a_runtime_and_a_digest_image_is_available(make):
    env, _ = make()
    r = bw.readiness(env, FAST)
    assert (r.available, r.reason) == (True, None)
    assert bw.describe(r) == {"available": True, "reason": None, "version": "5.2.4"}


def test_any_changed_file_in_the_tree_not_only_the_executable_is_unavailable(make):
    env, _ = make()
    (Path(env[bw.ENV_DIR]) / "5.2" / "scripts" / "startup.py").write_text(
        "import os  # tampered\n"
    )
    r = bw.readiness(env, FAST)
    assert not r.available and "pin" in r.reason


def test_a_pinned_image_missing_locally_is_unavailable_and_never_pulled(make):
    env, _ = make("no_image")
    r = bw.readiness(env, FAST)
    assert not r.available and "not present locally" in r.reason


def test_a_wrong_version_is_unavailable(make):
    env, _ = make("wrong_version")
    r = bw.readiness(env, FAST)
    assert not r.available and "5.2.4" in r.reason


def test_a_version_check_that_hangs_times_out_as_unavailable(make):
    env, _ = make("slow_version")
    t0 = time.monotonic()
    r = bw.readiness(env, FAST)
    assert not r.available and "exceeded" in r.reason
    assert time.monotonic() - t0 < 15


@pytest.mark.parametrize("name", [bw.ENV_DIR, bw.ENV_RUNTIME, bw.ENV_IMAGE])
def test_each_missing_setting_is_unavailable_and_named(make, name):
    env, _ = make()
    env.pop(name)
    r = bw.readiness(env, FAST)
    assert not r.available and name in r.reason


@pytest.mark.parametrize(
    "field, value, needle",
    [
        (bw.ENV_IMAGE, "blender-runtime:latest", "digest"),
        (bw.ENV_IMAGE, "x@sha256:" + "a" * 63, "digest"),
        (bw.ENV_RUNTIME, "/bin/sh", "docker or podman"),
        (bw.ENV_RUNTIME, "docker", "docker or podman"),
        (bw.ENV_DIR, "relative/blender", bw.ENV_DIR),
    ],
)
def test_a_malformed_setting_is_unavailable(make, field, value, needle):
    env, _ = make()
    env[field] = value
    r = bw.readiness(env, FAST)
    assert not r.available and needle in r.reason


def test_readiness_never_raises_when_the_process_cannot_start(make, monkeypatch):
    env, _ = make()

    def boom(*a, **k):
        raise subprocess.SubprocessError("preexec failed")

    monkeypatch.setattr(bw.subprocess, "Popen", boom)
    r = bw.readiness(env, FAST)
    assert not r.available and "SubprocessError" in r.reason


def test_readiness_is_cached_for_its_ttl(make, monkeypatch):
    env, _ = make()
    assert bw.readiness(env, FAST).available
    monkeypatch.setattr(
        bw, "_check_version", lambda *a: pytest.fail("not re-checked inside the TTL")
    )
    assert bw.readiness(env, FAST).available


def test_run_refuses_when_unavailable():
    with pytest.raises(bw.WorkerError) as e:
        bw.run(bw.Readiness(False, "why"), b"glb", None, FAST, {})
    assert (e.value.kind, e.value.message) == (bw.UNAVAILABLE, "why")


# ── the command line ─────────────────────────────────────────────────────────


def test_the_container_command_is_built_whole_with_every_isolation_flag(tmp_path):
    ready = bw.Readiness(True, None, "/opt/b", "/usr/bin/docker", IMAGE)
    argv = bw._container_argv(ready, "n1", tmp_path, ["--version"], bw.Caps())
    i = argv.index(IMAGE)
    head, cmd = argv[:i], argv[i + 1 :]
    assert cmd == [
        "/opt/blender/blender",
        "--background",
        "--factory-startup",
        "--offline-mode",
        "--version",
    ]
    pairs = list(zip(head, head[1:], strict=False))
    for flag, value in [
        ("--network", "none"),
        ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges"),
        ("--memory", str(2 << 30)),
        ("--memory-swap", str(2 << 30)),
        ("--name", "n1"),
        ("--ulimit", "cpu=120:125"),
        ("--ulimit", "core=0:0"),
    ]:
        assert (flag, value) in pairs, flag
    assert ("--pull", "never") in pairs
    for flag in ("--rm", "--init", "--read-only"):
        assert flag in head
    assert head.count("--network") == 1 and head.count("--mount") == 2
    assert f"type=bind,source={tmp_path},target=/work" in head
    assert "type=bind,source=/opt/b,target=/opt/blender,readonly" in head
    assert "--privileged" not in argv and "--python-use-system-env" not in argv
    assert head[head.index("--user") + 1].split(":")[0] != "0"


def test_the_trusted_script_imports_and_exports_with_extras():
    assert "import_scene_extras=True" in bw.SCRIPT and "export_extras=True" in bw.SCRIPT
    compile(bw.SCRIPT, "run.py", "exec")


# ── a run ────────────────────────────────────────────────────────────────────


def test_a_run_returns_both_outputs_and_removes_its_work_dir(make):
    env, rec = make()
    out = bw.run(_ready(env), b"GLB-IN", None, FAST, env)
    assert (out.glb, out.blend) == (b"GLB-IN", b"BLENDER-v502")
    assert "DONE stub" in out.log_tail
    seen = json.loads(rec.read_text())
    assert seen["args"][:4] == [
        "--background",
        "--factory-startup",
        "--offline-mode",
        "--python-exit-code",
    ]
    assert not Path(seen["cwd"]).exists()


def test_b3_the_runtime_cli_sees_only_the_allowlisted_environment(make):
    env, rec = make()
    env.update(
        {
            "SENTINEL_SECRET": "s3cr3t",
            "LD_PRELOAD": "/x.so",
            "PYTHONPATH": "/evil",
            "DATABASE_URL": "postgres://u:p@h/d",
            "ANTHROPIC_API_KEY": "k",
            "DOCKER_HOST": "unix:///run/docker.sock",
        }
    )
    bw.run(_ready(env), b"g", None, FAST, env)
    cli = json.loads(Path(str(rec) + ".cli").read_text())["env"]
    cli.pop("LC_CTYPE", None)  # CPython may set it under C.UTF-8
    assert cli == {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "DOCKER_HOST": "unix:///run/docker.sock",
    }
    argv = json.loads(Path(str(rec) + ".cli").read_text())["argv"]
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--env"] == [
        "HOME=/work",
        "TMPDIR=/tmp",
        "LANG=C.UTF-8",
    ]


# ── B4: cancel and timeout stop the container, not only the CLI ─────────────


def _wait_dead(*pids, timeout=5.0):
    deadline = time.monotonic() + timeout
    while any(_alive(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.05)
    return [p for p in pids if _alive(p)]


def test_b4_cancel_kills_the_detached_container_including_a_grandchild_ignoring_sigterm(
    make,
):
    env, rec = make("hang")
    ready = _ready(env)
    stop = threading.Event()
    kid_file = Path(str(rec) + ".kid")

    def cancel_when_started():
        deadline = time.monotonic() + 15
        while not kid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        stop.set()

    threading.Thread(target=cancel_when_started, daemon=True).start()
    with pytest.raises(bw.WorkerError) as e:
        bw.run(ready, b"g", stop, FAST, env)
    assert e.value.kind == bw.CANCELLED
    seen = json.loads(rec.read_text())
    assert _wait_dead(seen["pid"], int(kid_file.read_text())) == []
    assert not Path(seen["cwd"]).exists()


def test_b4_without_the_runtime_kill_the_detached_container_would_survive(
    make, monkeypatch
):
    # Proves the fake models containerd: killing the CLI's group alone leaves Blender running.
    env, rec = make("hang")
    ready = _ready(env)
    monkeypatch.setattr(bw, "_kill_container", lambda *a: None)
    with pytest.raises(bw.WorkerError):
        bw.run(ready, b"g", None, _caps(wall_s=1.5), env)
    pid = json.loads(rec.read_text())["pid"]
    try:
        assert _alive(pid)
    finally:
        os.killpg(pid, 9)


def test_two_concurrent_runs_get_distinct_names_and_cancelling_one_leaves_the_other(
    make,
):
    env, _ = make("hang")
    ready = _ready(env)
    state = make.state("hang")
    stops = [threading.Event(), threading.Event()]
    errors = []

    def go(stop):
        try:
            bw.run(ready, b"g", stop, FAST, env)
        except bw.WorkerError as exc:
            errors.append(exc.kind)

    before = {p.name for p in state.iterdir()}  # the readiness --version run
    threads = [threading.Thread(target=go, args=(s,), daemon=True) for s in stops]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 15
    while (
        len({p.name for p in state.iterdir()} - before) < 2
        and time.monotonic() < deadline
    ):
        time.sleep(0.05)
    names = sorted({p.name for p in state.iterdir()} - before)
    assert len(names) == 2 and names[0] != names[1]
    assert all(
        n.startswith("pypsa-blender-") and len(n) == len("pypsa-blender-") + 32
        for n in names
    )
    pids = [int((state / n).read_text()) for n in names]
    stops[0].set()
    threads[0].join(15)
    alive = [p for p in pids if _alive(p)]
    assert len(alive) == 1, "cancelling one run must leave the other running"
    stops[1].set()
    threads[1].join(15)
    assert _wait_dead(*pids) == [] and errors == [bw.CANCELLED, bw.CANCELLED]


def test_b5_the_wall_timeout_is_typed_and_stops_the_container(make):
    env, rec = make("hang")
    with pytest.raises(bw.WorkerError) as e:
        bw.run(_ready(env), b"g", None, _caps(wall_s=1.5), env)
    assert e.value.kind == bw.TIMEOUT
    assert _wait_dead(json.loads(rec.read_text())["pid"]) == []


# ── B5: each cap is a typed kind ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "mode, kind",
    [
        ("cpu", bw.CPU_LIMIT),
        ("fsize", bw.OUTPUT_LIMIT),
        ("memory", bw.MEMORY_LIMIT),
        ("crash", bw.FAILED),
        ("no_output", bw.FAILED),
        ("symlink", bw.FAILED),
    ],
)
def test_b5_each_cap_and_failure_is_typed_and_leaves_no_work_dir(make, mode, kind):
    env, rec = make(mode)
    with pytest.raises(bw.WorkerError) as e:
        bw.run(_ready(env), b"g", None, _caps(cpu_s=1), env)
    assert e.value.kind == kind, e.value
    assert not Path(json.loads(rec.read_text())["cwd"]).exists()


def test_b5_an_output_over_the_size_cap_is_typed(make):
    env, _ = make("big")
    with pytest.raises(bw.WorkerError) as e:
        bw.run(_ready(env), b"x" * 100, None, _caps(output_bytes=1000), env)
    assert e.value.kind == bw.OUTPUT_LIMIT


def test_a_symlinked_output_is_never_followed(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("db")
    (tmp_path / "out.blend").symlink_to(secret)
    with pytest.raises(bw.WorkerError) as e:
        bw._read_output(tmp_path, "out.blend", 1 << 20)
    assert e.value.kind == bw.FAILED and "db" not in str(e.value)


def test_a_fifo_output_is_refused_without_blocking(tmp_path):
    os.mkfifo(tmp_path / "out.glb")
    with pytest.raises(bw.WorkerError) as e:
        bw._read_output(tmp_path, "out.glb", 1 << 20)
    assert e.value.kind == bw.FAILED


def test_an_unclassified_kind_is_refused():
    with pytest.raises(ValueError):
        bw.WorkerError("nope", "x")
