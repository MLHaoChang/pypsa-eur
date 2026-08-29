"""Typed stage-failure artifact — UI and chatbot render this same file."""
import dataclasses
import json
import re
from pathlib import Path


@dataclasses.dataclass
class StageError:
    stage: str
    element_ids: list
    cause: str

    def write(self, outdir) -> Path:
        # `stage` is a public field, so it is caller-shaped, not one of the four
        # driver literals. Sanitizing the component keeps the artifact inside
        # outdir instead of letting "../.." pick the directory.
        stage = re.sub(r"[^A-Za-z0-9_-]", "_", self.stage)
        p = Path(outdir) / f"error_{stage}.json"
        p.write_text(json.dumps(dataclasses.asdict(self), indent=2))
        return p
