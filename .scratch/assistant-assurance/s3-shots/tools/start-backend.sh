#!/usr/bin/env bash
# Start the patched backend (launcher.py) from the throwaway wt-s3-shots checkout.
set -euo pipefail
TOOLS="$(cd "$(dirname "$0")" && pwd)"
export PYPSAGUI_APP_DATA_DIR="$TOOLS/../appdata"
export PYPSA_GUI_ANSWER_ASSURANCE_PROFILES="objective-bridge/1"
export PYPSA_GUI_ANSWER_ASSURANCE_MODE="strict"
# Dummy value only so the chat panel is enabled; the provider is scripted and never calls out.
export ANTHROPIC_API_KEY="sk-ant-shots-dummy-not-a-key"
cd /home/user/wt-s3-shots/pypsa-gui/backend
exec /root/.venv-pypsa-gui/bin/python "$TOOLS/launcher.py"
