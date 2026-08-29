"""Typed stage-failure artifact — UI and chatbot render this same file."""
import dataclasses
import json
from pathlib import Path


@dataclasses.dataclass
class StageError:
    stage: str
    element_ids: list
    cause: str

    def write(self, outdir) -> Path:
        p = Path(outdir) / f"error_{self.stage}.json"
        p.write_text(json.dumps(dataclasses.asdict(self), indent=2))
        return p
