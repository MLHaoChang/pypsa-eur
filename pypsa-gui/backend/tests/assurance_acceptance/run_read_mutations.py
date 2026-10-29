"""Dev helper: each checked section/compare guard mutant fails a named test.

GK B (12) condition 9 for the run evidence PR 2. Each mutant is one source
edit; the script applies it, runs the named tests in a fresh interpreter,
restores the file, and reports whether a test failed. Run from
pypsa-gui/backend on a clean tree:

    python tests/assurance_acceptance/run_read_mutations.py
"""

import pathlib
import subprocess
import sys

SECTIONS = "tests/assurance_acceptance/test_p1_run_sections.py"
COMPARE = "tests/assurance_acceptance/test_p1_run_compare.py"

# name: (file, old, new, tests that must fail)
MUTANTS = {
    "section_lookup_skips_fact_rederive": (
        "services/assurance_records.py",
        """                unavailable("evidence_unavailable")
            _require_current_facts(record["facts"], facts)""",
        """                unavailable("evidence_unavailable")""",
        [f"{SECTIONS}::test_a_rewritten_evidence_fact_is_unavailable"],
    ),
    "section_lookup_skips_source_digest": (
        "services/assurance_records.py",
        """                or digest({"manifest": manifest, "section": scope["section"]})
                != record["source_sha256"]
                or _run_section_scope""",
        """                or _run_section_scope""",
        [f"{SECTIONS}::test_another_sealed_manifest_for_the_run_makes_it_unavailable"],
    ),
    "section_guard_accepts_extra_args": (
        "harness/assurance.py",
        """            if set(args) == {"run_id", "section"}:""",
        """            if "section" in args:""",
        [f"{SECTIONS}::test_inexact_section_args_are_refused_before_the_handler"],
    ),
    "section_fact_accepts_bool": (
        "services/assurance_records.py",
        """    return type(value) in (int, float) and math.isfinite(value)""",
        """    return isinstance(value, (int, float)) and math.isfinite(value)""",
        [f"{SECTIONS}::test_a_section_whose_only_pair_is_a_bool_is_refused"],
    ),
    "claim_matches_first_fact_by_metric": (
        "harness/assurance.py",
        """            fact = next((f for f in record["facts"] if f["path"] == source.path), None)""",
        """            fact = next((f for f in record["facts"] if f["metric"] == block.metric), None)""",
        [f"{SECTIONS}::test_a_repeated_key_is_matched_by_its_path"],
    ),
    "compare_guard_accepts_allow_input_changes": (
        "harness/assurance.py",
        """                or args.get("allow_input_changes", False) is not False""",
        "",
        [f"{COMPARE}::test_inexact_compare_args_are_refused_before_the_handler"],
    ),
    "compare_lookup_skips_recompute": (
        "services/assurance_records.py",
        """            _require_current_facts(record["facts"], run_comparison_facts(sources))""",
        """            pass""",
        [f"{COMPARE}::test_a_rewritten_evidence_fact_is_unavailable"],
    ),
    "compare_lookup_skips_source_digest": (
        "services/assurance_records.py",
        """                or _comparison_digest(sources, scope["metrics"]) != record["source_sha256"]
""",
        "",
        [
            f"{COMPARE}::test_another_sealed_manifest_on_either_side_makes_it_unavailable"
        ],
    ),
}


def main():
    survivors = []
    for name, (path, old, new, tests) in MUTANTS.items():
        file = pathlib.Path(path)
        source = file.read_text(encoding="utf-8")
        if source.count(old) != 1:
            print(f"{name}: mutation site not found once", file=sys.stderr)
            survivors.append(name)
            continue
        file.write_text(source.replace(old, new), encoding="utf-8")
        try:
            rc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-x",
                    "-p",
                    "no:cacheprovider",
                    "-W",
                    "ignore",
                    "--no-header",
                    *tests,
                ],
                capture_output=True,
            ).returncode
        finally:
            file.write_text(source, encoding="utf-8")
        print(f"{name}: {'killed' if rc != 0 else 'SURVIVED'}", file=sys.stderr)
        if rc == 0:
            survivors.append(name)
    print(f"{len(survivors)} surviving: {survivors}", file=sys.stderr)
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
