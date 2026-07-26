from __future__ import annotations

import uuid
from pathlib import Path

from settings import get_settings


def storage_path_for(org_id: uuid.UUID, project_id: uuid.UUID) -> Path:
    return get_settings().projects_root / str(org_id) / str(project_id)
