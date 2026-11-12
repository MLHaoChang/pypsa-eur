"""
Issue 07 pilot acceptance, offline and bounded (GK B (33) P8, L7).

Runs the pilot tests (an eh_site@2 run from one intake to the hand-off: the
real solve, EH study and coupling loop, the fake report provider) in a child
pytest process that loads this module as a plugin, so every socket the run
opens is checked by `install_guard`: a connect to anything but loopback (or a
Unix socket) raises `NetworkRefused`, and so does resolving a non-local name.

The driver itself bounds the child at `BOUND_S` wall-clock seconds and kills
its whole process group when it overruns, so a hang fails cleanly.

Run only through the `gui-qa-drivers` pixi task (`tests/qa_harness_pilot.py`):

    pixi run gui-qa-drivers
"""

from __future__ import annotations

import ipaddress
import os
import pathlib
import signal
import socket
import subprocess
import sys

BOUND_S = 300
TARGETS = (
    "tests/test_harness_run_eh_site_v2.py::test_pilot_envelope_mode_reaches_the_hand_off",
    "tests/test_harness_run_stepwise_intake.py::test_pilot_stepwise_mode_reaches_the_hand_off_with_one_card_per_write_or_run",
    # Issue 33 (L7): eh_site@3 fetches the named site's map context from a fixture.
    "tests/test_harness_run_eh_site_v3.py::test_pilot_v3_fetches_the_named_site_offline_and_the_rule_passes",
)
_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", ""}


class NetworkRefused(OSError):
    """A non-loopback connect or name lookup during the offline pilot."""


def _local(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host).split("%", 1)[0]
    if host.lower() in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def install_guard() -> None:
    """Refuse every non-loopback connect and name lookup in this process."""
    if getattr(socket.socket.connect, "_pilot_guard", False):
        return
    connect, connect_ex, getaddrinfo = (
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.getaddrinfo,
    )

    def check(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not _local(address[0]):
            raise NetworkRefused(
                f"offline pilot: connect to {address[0]!r} refused"  # repr-ok: test-driver error
            )

    def guarded_connect(self, address):
        check(self, address)
        return connect(self, address)

    def guarded_connect_ex(self, address):
        check(self, address)
        return connect_ex(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if not _local(host):
            raise NetworkRefused(
                f"offline pilot: name lookup of {host!r} refused"  # repr-ok: test-driver error
            )
        return getaddrinfo(host, *args, **kwargs)

    for fn in (guarded_connect, guarded_connect_ex, guarded_getaddrinfo):
        fn._pilot_guard = True
    socket.socket.connect, socket.socket.connect_ex = (
        guarded_connect,
        guarded_connect_ex,
    )
    socket.getaddrinfo = guarded_getaddrinfo


def pytest_configure(config):  # noqa: ARG001 — the child process loads this module as a plugin
    install_guard()


def main() -> int:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-x",
        "-p",
        "smoke.accept_harness_pilot",
        *TARGETS,
    ]
    proc = subprocess.Popen(cmd, cwd=_BACKEND, start_new_session=True)
    try:
        code = proc.wait(timeout=BOUND_S)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        print(f"[FAIL] harness pilot: over the {BOUND_S} s bound, killed")
        return 1
    print(
        f"[{'PASS' if code == 0 else 'FAIL'}] harness pilot (offline, bound {BOUND_S} s): pytest exit {code}"
    )
    return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
