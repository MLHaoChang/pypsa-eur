"""Child targets for the Excel export failure-path tests (importable, so `spawn` can run them)."""

import time


def raises(nc_path, tmp, conn):
    conn.send("ValueError: boom")
    conn.close()


def exits(nc_path, tmp, conn):
    conn.close()


def hangs(nc_path, tmp, conn):
    time.sleep(60)
