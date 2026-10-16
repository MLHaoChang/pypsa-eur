"""Compare two directories written by ``test_campus_island_equivalence.py``
(two commits, or two worker counts): every file of every site's run
directory byte for byte. The manifest is compared without the keys that
name how and where the run was made rather than what it found: the saved
network's path (a fresh temporary project each run), its SHA-256 (the
netCDF export's bytes vary with the process's ``PYTHONHASHSEED``, the same
network giving the same bytes only under the same seed) and ``workers``.

    python tests/campus_island_equivalence.py A B

Exits 1 on any difference, listing each one.
"""
import json
import sys
from pathlib import Path

MANIFEST = "campus_manifest.json"
#: Manifest keys about the run's making, not its results.
MANIFEST_IGNORED = ("network_path", "network_sha256", "workers")


def _manifest(path):
    return {k: v for k, v in json.loads(path.read_text()).items() if k not in MANIFEST_IGNORED}


def compare(a: Path, b: Path) -> list:
    """The differences between ``a`` and ``b``, as lines; [] when none."""
    diffs = []
    sites = sorted(p.name for p in a.iterdir() if p.is_dir())
    if sites != sorted(p.name for p in b.iterdir() if p.is_dir()):
        diffs.append(f"sites differ: {sites} vs {sorted(p.name for p in b.iterdir() if p.is_dir())}")
    for site in sites:
        fa = {p.relative_to(a / site) for p in (a / site).rglob("*") if p.is_file()}
        fb = {p.relative_to(b / site) for p in (b / site).rglob("*") if p.is_file()} if (b / site).is_dir() else set()
        for rel in sorted(fa ^ fb):
            diffs.append(f"{site}/{rel}: only in {'A' if rel in fa else 'B'}")
        for rel in sorted(fa & fb):
            pa, pb = a / site / rel, b / site / rel
            same = (_manifest(pa) == _manifest(pb)) if rel.name == MANIFEST else pa.read_bytes() == pb.read_bytes()
            if not same:
                diffs.append(f"{site}/{rel}: differs")
        print(f"{site}: {len(fa & fb)} files compared", file=sys.stderr)
    return diffs


if __name__ == "__main__":
    out = compare(Path(sys.argv[1]), Path(sys.argv[2]))
    print("\n".join(out) if out else "identical")
    sys.exit(1 if out else 0)
