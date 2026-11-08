"""
Issue 07 pilot acceptance, offline and bounded: runs
`smoke/accept_harness_pilot.py` (GK B (33) P8). See that file.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from smoke.accept_harness_pilot import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
