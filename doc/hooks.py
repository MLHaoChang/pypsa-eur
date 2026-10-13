# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _collate_release_notes():
    path = ROOT / "scripts" / "collate_release_notes.py"
    spec = importlib.util.spec_from_file_location("collate_release_notes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def on_page_markdown(markdown, page, config, files, **kwargs):
    import re

    markdown = re.sub(r"(\[\^[^\]]+\]:.*)\n(\[\^)", r"\1\n\n\2", markdown)
    if page.file.src_uri == "release_notes.md":
        # Show the pending `doc/release-notes.d/` fragments as upcoming entries.
        collate = _collate_release_notes()
        fragments = [f.read_text() for f in collate.fragment_files(collate.FRAGMENTS)]
        markdown = collate.render(markdown, fragments)
    return markdown


def on_post_build(config, **kwargs):
    import requests

    url = "https://zenodo.org/records/14144752/files/map.html?download=1"
    out = os.path.join(config["site_dir"], "base-network-raw.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(requests.get(url).text)
