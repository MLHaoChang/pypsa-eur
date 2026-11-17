"""Issue 06 slice (a): the sandboxed Blender worker, with a fake Blender.

CI has no Blender, so a Python stub honours the same CLI: `--version`, or
`--python run.py -- IN OUT.glb OUT.blend`. A fake container wrapper checks it
was given the isolation flags, then execs the rest of its argv. GK B's slice (a)
conditions are B2 (readiness pin), B3 (env allowlist), B4 (group kill, temp dir
removed) and B5 (each cap refused with a typed kind).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from services.site_scene import blender_worker as bw

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="the worker is Linux-only (/proc, process groups)"
)

WRAPPER = """\
import os, sys
a = sys.argv[1:]
i = a.index("--")
assert {"--network", "none", "--read-only", "--user", "--memory"} <= set(a[:i]), a
os.execv(a[i + 1], a[i + 1:])
"""

STUB = """\
#!{python}
import json, os, signal, subprocess, sys, time
MODE = {mode!r}
REC = {rec!r}
args = sys.argv[1:]
if "--version" in args:
    if MODE == "slow_version":
        time.sleep(60)
    print("Blender 9.9.9" if MODE == "wrong_version" else "Blender 5.2.4 LTS")
    print("\\tbuild hash: stub")
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
    signal.signal(signal.SIGXFSZ, signal.SIG_DFL)   # as Blender (C) would
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
if MODE == "orphan":
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    with open(REC + ".kid", "w") as f:
        f.write("pending")
data = open(src, "rb").read()
open(out_glb, "wb").write(data * (100 if MODE == "big" else 1))
open(out_blend, "wb").write(b"BLENDER-v502")
print("DONE stub")
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); it is dead for our purposes.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ")[1][0] != "Z"
    except OSError:
        return False


@pytest.fixture
def make(tmp_path, monkeypatch):
    """`make(mode)` -> (Readiness, record path) for a stub in that mode."""
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(WRAPPER)

    def build(mode: str = "ok"):
        d = tmp_path / mode
        d.mkdir()
        rec = d / "record.json"
        stub = d / "blender"
        stub.write_text(STUB.format(python=sys.executable, mode=mode, rec=str(rec)))
        stub.chmod(0o755)
        monkeypatch.setattr(
            bw, "BINARY_SHA256", hashlib.sha256(stub.read_bytes()).hexdigest()
        )
        argv = [
            sys.executable,
            str(wrapper),
            "--network",
            "none",
            "--read-only",
            "--user",
            "1000:1000",
            "--memory",
            "1g",
            "--",
            "{blender}",
        ]
        env = {bw.ENV_BINARY: str(stub), bw.ENV_WRAPPER: json.dumps(argv)}
        return env, rec

    return build


FAST = bw.Caps(
    wall_s=20,
    cpu_s=2,
    rss_bytes=200 << 20,
    fsize_bytes=1 << 20,
    output_bytes=1 << 20,
    grace_s=1.0,
    version_timeout_s=2.0,
    poll_s=0.05,
)


def _ready(env, caps=FAST):
    r = bw.readiness(env, caps)
    assert r.available, r.reason
    return r


# ── B2 readiness ─────────────────────────────────────────────────────────────


def test_a_ready_stub_is_available(make):
    env, _ = make()
    r = bw.readiness(env, FAST)
    assert (r.available, r.reason) == (True, None)
    assert bw.describe(r) == {"available": True, "reason": None, "version": "5.2.4"}


def test_a_binary_whose_sha256_differs_from_the_pin_is_unavailable(make, monkeypatch):
    env, _ = make()
    monkeypatch.setattr(bw, "BINARY_SHA256", "0" * 64)
    r = bw.readiness(env, FAST)
    assert not r.available and "sha256" in r.reason


def test_a_wrong_version_is_unavailable(make):
    env, _ = make("wrong_version")
    r = bw.readiness(env, FAST)
    assert not r.available and "5.2.4" in r.reason


def test_a_version_check_that_hangs_times_out_as_unavailable(make):
    env, _ = make("slow_version")
    t0 = time.monotonic()
    r = bw.readiness(env, FAST)
    assert not r.available and "exceeded" in r.reason
    assert time.monotonic() - t0 < 10


@pytest.mark.parametrize(
    "raw, needle",
    [
        (None, "not set"),
        ("not json", "JSON list"),
        ("[]", "non-empty"),
        ('["docker", "run"]', "{blender}"),
        (
            '["docker", "run", "--read-only", "--user", "1", "--memory", "1g", "{blender}"]',
            "--network none",
        ),
        (
            '["docker", "run", "--network", "host", "--read-only", "--user", "1", "--memory", "1g", "{blender}"]',
            "--network none",
        ),
        (
            '["docker", "run", "--network=none", "--user", "1", "--memory", "1g", "{blender}"]',
            "--read-only",
        ),
        (
            '["docker", "run", "--network=none", "--read-only", "--memory", "1g", "{blender}"]',
            "--user",
        ),
        (
            '["docker", "run", "--network=none", "--read-only", "--user=1", "{blender}"]',
            "--memory",
        ),
    ],
)
def test_no_container_wrapper_or_one_missing_an_isolation_flag_is_unavailable(
    make, raw, needle
):
    env, _ = make()
    if raw is None:
        env.pop(bw.ENV_WRAPPER)
    else:
        env[bw.ENV_WRAPPER] = raw
    r = bw.readiness(env, FAST)
    assert not r.available and needle in r.reason


def test_the_equals_forms_of_the_wrapper_flags_are_accepted():
    argv = bw.parse_wrapper(
        '["d", "--network=none", "--read-only", "--user=1", "--memory=1g", "{blender}"]'
    )
    assert argv[-1] == "{blender}"


@pytest.mark.parametrize(
    "value", [None, "", "relative/blender", "/nonexistent/blender"]
)
def test_a_missing_or_unusable_binary_is_unavailable(make, value):
    env, _ = make()
    if value is None:
        env.pop(bw.ENV_BINARY)
    else:
        env[bw.ENV_BINARY] = value
    r = bw.readiness(env, FAST)
    assert not r.available and bw.ENV_BINARY in r.reason


def test_run_refuses_when_unavailable():
    with pytest.raises(bw.WorkerError) as e:
        bw.run(bw.Readiness(False, "why"), b"glb", None, FAST, {})
    assert (e.value.kind, e.value.message) == (bw.UNAVAILABLE, "why")


# ── a run ────────────────────────────────────────────────────────────────────


def test_a_run_returns_both_outputs_with_the_pinned_flags_and_removes_its_work_dir(
    make,
):
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
    assert "--python-use-system-env" not in seen["args"]
    work = Path(seen["cwd"])
    assert not work.exists()
    assert seen["args"][seen["args"].index("--python") + 1] == str(work / "run.py")


def test_the_trusted_script_imports_and_exports_with_extras():
    assert "import_scene_extras=True" in bw.SCRIPT
    assert "export_extras=True" in bw.SCRIPT
    compile(bw.SCRIPT, "run.py", "exec")


def test_b3_the_child_sees_only_the_allowlisted_environment(make):
    env, rec = make()
    env.update(
        {
            "SENTINEL_SECRET": "s3cr3t",
            "LD_PRELOAD": "/x.so",
            "PYTHONPATH": "/evil",
            "PYTHONSTARTUP": "/evil.py",
            "DATABASE_URL": "postgres://u:p@h/d",
            "ANTHROPIC_API_KEY": "k",
            "DOCKER_HOST": "unix:///run/docker.sock",
        }
    )
    bw.run(_ready(env), b"g", None, FAST, env)
    child = json.loads(rec.read_text())["env"]
    child.pop("LC_CTYPE", None)  # CPython may set it under C.UTF-8
    work = json.loads(rec.read_text())["cwd"]
    assert child == {
        "PATH": "/usr/bin:/bin",
        "HOME": work,
        "TMPDIR": work,
        "LANG": "C.UTF-8",
        "DOCKER_HOST": "unix:///run/docker.sock",
    }
    assert "s3cr3t" not in json.dumps(child)


# ── B4 kill ──────────────────────────────────────────────────────────────────


def test_b4_cancel_kills_the_group_including_a_grandchild_that_ignores_sigterm(make):
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
    t0 = time.monotonic()
    with pytest.raises(bw.WorkerError) as e:
        bw.run(ready, b"g", stop, FAST, env)
    assert e.value.kind == bw.CANCELLED
    assert time.monotonic() - t0 < 15
    seen = json.loads(rec.read_text())
    kid = int(kid_file.read_text())
    for pid in (seen["pid"], kid):
        deadline = time.monotonic() + 3
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(pid), pid
    assert not Path(seen["cwd"]).exists()


def test_b4_a_grandchild_left_behind_by_a_successful_run_is_killed(make):
    env, rec = make("orphan")
    bw.run(_ready(env), b"g", None, FAST, env)
    seen = json.loads(rec.read_text())
    pgid = seen["pid"]
    deadline = time.monotonic() + 3
    while bw._group_rss(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert bw._group_rss(pgid) == 0


# ── B5 caps, each a typed kind ───────────────────────────────────────────────


def test_b5_the_wall_timeout_is_typed_and_kills_the_group(make):
    env, rec = make("hang")
    caps = bw.Caps(**{**FAST.__dict__, "wall_s": 1.0})
    with pytest.raises(bw.WorkerError) as e:
        bw.run(_ready(env), b"g", None, caps, env)
    assert e.value.kind == bw.TIMEOUT
    assert not _alive(json.loads(rec.read_text())["pid"])


def test_b5_the_cpu_cap_is_typed(make):
    env, _ = make("cpu")
    with pytest.raises(bw.WorkerError) as e:
        bw.run(_ready(env), b"g", None, bw.Caps(**{**FAST.__dict__, "cpu_s": 1}), env)
    assert e.value.kind == bw.CPU_LIMIT


def test_b5_the_file_size_cap_is_typed(make):
    env, _ = make("fsize")
    with pytest.raises(bw.WorkerError) as e:
        bw.run(_ready(env), b"g", None, FAST, env)
    assert e.value.kind == bw.OUTPUT_LIMIT


def test_b5_the_resident_memory_cap_is_typed(make):
    env, _ = make("memory")
    with pytest.raises(bw.WorkerError) as e:
        bw.run(_ready(env), b"g", None, FAST, env)
    assert e.value.kind == bw.MEMORY_LIMIT


def test_b5_an_output_over_the_size_cap_is_typed(make):
    env, _ = make("big")
    caps = bw.Caps(**{**FAST.__dict__, "output_bytes": 1000})
    with pytest.raises(bw.WorkerError) as e:
        bw.run(_ready(env), b"x" * 100, None, caps, env)
    assert e.value.kind == bw.OUTPUT_LIMIT


@pytest.mark.parametrize("mode", ["crash", "no_output"])
def test_a_failing_or_silent_run_is_failed_and_leaves_no_work_dir(make, mode):
    env, rec = make(mode)
    with pytest.raises(bw.WorkerError) as e:
        bw.run(_ready(env), b"g", None, FAST, env)
    assert e.value.kind == bw.FAILED
    assert not Path(json.loads(rec.read_text())["cwd"]).exists()


def test_an_unclassified_kind_is_refused():
    with pytest.raises(ValueError):
        bw.WorkerError("nope", "x")
