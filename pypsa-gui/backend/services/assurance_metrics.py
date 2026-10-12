"""Compatibility alias; checked calculation contracts belong to the harness."""

import sys
from harness import calculations

sys.modules[__name__] = calculations
