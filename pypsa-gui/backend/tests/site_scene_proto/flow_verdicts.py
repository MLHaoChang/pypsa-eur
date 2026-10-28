"""SC-01a promoted this prototype module to production: `services.site_scene.verdicts`.
This alias keeps the SC-00 N tests running against the production code."""
import sys

from services.site_scene import verdicts as _production

sys.modules[__name__] = _production
