# 10 — Ship the harness Markdown in the desktop bundle

Status: ready-for-agent (done 2026-10-05: three datas entries, three ROOTED probes in check_bundle)
Type: task
Blocked by: 01

Add `harness/prompts`, `harness/workflows`, `harness/skills` to the `datas`
allowlist in `pypsa-gui.spec` (the file is an allowlist by design) and make
`smoke/check_bundle.py` assert they are present. The loaders resolve paths
with `Path(__file__)`, which works under `_MEIPASS` when the datas keep the
package-relative layout.

Done when: `check_bundle.py` fails red when a folder is removed from `datas`
and passes with it present.
