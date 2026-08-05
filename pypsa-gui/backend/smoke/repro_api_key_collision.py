"""Reproduce the two-store API-key collision on the current branch.

Runs the exact startup sequence main.py performs, against a throwaway app-data
directory, with a DIFFERENT key in each of the two stores so the winner is
unambiguous.
"""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path("/Users/orange/Desktop/Code Test/pypsa-eur/pypsa-gui/backend")
sys.path.insert(0, str(BACKEND))

tmp = Path(tempfile.mkdtemp(prefix="keycollide-"))
os.environ["PYPSAGUI_APP_DATA_DIR"] = str(tmp)
os.environ.pop("ANTHROPIC_API_KEY", None)

import app_paths  # noqa: E402
import local_settings  # noqa: E402
from services import app_secrets  # noqa: E402

USER_ENV_KEY = "sk-ant-FROM-user-env-AAAA"
JSON_KEY = "sk-ant-FROM-local-settings-json-BBBB"

# Store 1: master's user.env (written by ApiKeySetup.tsx via app_secrets)
app_secrets._write_managed({"ANTHROPIC_API_KEY": USER_ENV_KEY})
# Store 2: the feature branch's local-settings.json (written by LocalSettings.tsx)
local_settings.write_api_key(JSON_KEY)

print(f"app-data:      {tmp}")
print(f"user.env            -> {USER_ENV_KEY}")
print(f"local-settings.json -> {JSON_KEY}")
print()

# --- exactly what main.py does, in order -----------------------------------
app_secrets.bootstrap_environment(backend_env=BACKEND / ".env")
after_bootstrap = os.environ.get("ANTHROPIC_API_KEY")
applied = local_settings.apply_to_environ()
after_apply = os.environ.get("ANTHROPIC_API_KEY")

print("main.py line 20  bootstrap_environment() ->", after_bootstrap)
print("main.py line 47  apply_to_environ()      ->", f"returned {applied}")
print("live ANTHROPIC_API_KEY                   ->", after_apply)
print()

winner = "user.env" if after_apply == USER_ENV_KEY else "local-settings.json"
print(f"WINNER: {winner}")
print(f"apply_to_environ set anything? {applied}")
print()

# What each UI shows the user, side by side.
st = app_secrets.status()
print("ApiKeySetup.tsx  (app_secrets.status): "
      f"configured={st['configured']} source={st['source']} hint={st['hint']}")
stored = local_settings.stored_api_key()
print("LocalSettings.tsx (local_settings):    "
      f"key_set={stored is not None} hint={local_settings.api_key_hint(stored)}")
print()
print("The Settings pane advertises a key that is NOT the one in use."
      if stored and after_apply != stored else "stores agree")
