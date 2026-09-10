# The 43 baseline test failures, characterised — every one attributed, two of them security findings

**Status:** investigation complete. No code changes; two findings are handed
to the maintainer as decisions rather than fixed unilaterally (§3).

**Why this exists.** The PR's verification story states that the full backend
suite fails 43 tests identically on `master` and on the branch, and calls them
"environmental" — the container runs a pip stack, not the repository's pinned
pixi environment. Until now that was an assertion. Twelve had been traced
(`2026-09-07-myopic-assign-solution-investigation.md`); the other 31 had not
been examined at all, and four of them (`test_cost_totals_contract`,
`test_compare_invariants`) are named like numerical contracts, where a real
defect could hide behind the label. This note examines all 43.

**Method.** Each failing test was run in isolation with a full traceback, the
reasons grouped by root cause rather than by file, and every environmental
attribution checked against the actual missing thing (a module import, a
library, a platform path) rather than inferred from the test's name.

## 1. Attribution of all 43

| cause | tests | files | verdict |
|---|---|---|---|
| PyPSA `assign_solution → DataFrame.combine_first` raises `TypeError: Must pass list-like as names` on every myopic solve | **14** | `test_myopic_build_period_visibility` (5), `test_myopic_horizon_cost` (4), `test_myopic_summary_log` (2), `test_cost_totals_contract` (2), `test_myopic_feasibility` (1) | upstream `pypsa 1.3.0` / `pandas 3.0.5` interaction; see the myopic note. **Corrects that note's count from 12 to 14** — the two `test_cost_totals_contract` tests are myopic solves and were mis-grouped by filename. |
| `_SafeResultsUnpickler` refuses `pandas.DataFrame` as a disallowed global | **4** | `test_security_import` (2), `test_compare_invariants` (2) | **latent defect, masked by the pinned pandas — §3.1** |
| `pywebview` not installed | **7** | `test_desktop_bootstrap` (3), `test_desktop_downloads` (2), `test_shutdown` (1), `test_packaging_requirements` (1) | environmental. These tests **deliberately refuse to skip** ("must not be skipped — run `pixi run gui-tests`"), which is correct: a skipped desktop-safety test is a test that never runs. |
| `anthropic` SDK not installed | **7** | `test_local_settings_api` (7) | environmental — the API-key probe tests import the SDK to construct its exception classes |
| `pypdf` not installed | **7** | `test_chat_multimodal` (7) | environmental — PDF page-count and truncation |
| `python-magic` / libmagic not installed | **1** | `test_chat_uploads::test_post_renamed_exe_as_xlsx_still_rejected` | environmental in cause, **a security control failing open in effect — §3.2** |
| `psycopg` not installed | **1** | `test_sqlite_pragmas::test_non_sqlite_engine_is_returned_untouched` | environmental — the "non-sqlite engine" case needs a Postgres driver to construct |
| macOS data-directory layout asserted on Linux | **2** | `test_app_paths` (2) | environmental (platform). The tests build a fake home carrying `Library/Application Support/PyPSA GUI` — the macOS legacy location — and assert the resolver finds it; on Linux `app_data_dir()` resolves under `.local/share/`, which is right for Linux. |

**14 + 4 + 7 + 7 + 7 + 1 + 1 + 2 = 43.** Every failure has a named cause, and
none of the four "contract" tests hides a numerical defect: two are the myopic
upstream issue and two are the unpickler.

## 2. What the pinned environment would and would not settle

Running the suite under `pixi` (the container has no env manager on the path;
`pixi.toml` and the lock are present) would settle **39 of 43** directly: the
missing packages would be present, the platform tests would run where they
were written, and the myopic 14 would either pass — closing the upstream
question as a pip-stack artifact — or fail, which the myopic note says would
then call for a pandas bisect against PyPSA and an upstream report.

It would **not** settle the four unpickler failures in the sense that matters.
They would pass under a pinned pandas 2, but passing there is the *masking*,
not the resolution — see §3.1.

## 3. Two findings for the maintainer — decisions, not patches

Both are security controls whose behaviour is silently different in an
environment the code explicitly anticipates. Neither is changed here, because
each fix is a security-policy decision: one widens a trust allowlist, the other
reverses a documented design choice.

### 3.1 The results-state unpickler is pinned to pandas 2's module paths, and refuses every legitimate file on pandas 3

`routers/projects.py`'s `_SAFE_UNPICKLE_GLOBALS` enumerates the classes a
`results_state.pkl` may contain, by `(module, name)`. It lists
`("pandas.core.frame", "DataFrame")`, `("pandas.core.series", "Series")`,
`("pandas.core.indexes.base", "Index")`, and so on — the paths pandas 2
pickles under. Measured on the installed `pandas 3.0.5`, a pickled
`DataFrame` references these globals instead:

```
DataFrame     -> pandas.DataFrame, pandas.Index, pandas.RangeIndex, pandas.StringDtype,
                 pandas.arrays.ArrowStringArray, pandas._libs.internals._unpickle_block,
                 pandas.core.indexes.base._new_Index,
                 pandas.core.internals.managers.BlockManager,
                 pyarrow.lib._restore_array, pyarrow.lib.py_buffer, pyarrow.lib.type_for_alias,
                 builtins.bytearray, builtins.slice, numpy.*
Series        -> pandas.Series, pandas.RangeIndex, …
DatetimeIndex -> pandas.DatetimeIndex, pandas.arrays.DatetimeArray, …
MultiIndex    -> pandas.MultiIndex, pandas.Index, …
```

Two separate things changed. The six enumerated classes are now pickled under
their **top-level re-export** (`pandas.DataFrame`, not
`pandas.core.frame.DataFrame`) — a pure aliasing change. And pandas 3 defaults
string columns to Arrow storage, so any frame with a string column now also
references `pandas.StringDtype`, `pandas.arrays.ArrowStringArray` and three
**`pyarrow.lib` reconstruction functions** the allowlist has never contained.

The consequence in the app: `_safe_unpickle_results` raises, the caller
catches it, and the lost-load comparison reports `available=False`. That is
the **safe** failure direction — the unpickler refuses rather than trusts —
but it is **silent**: the user sees an empty tab, not "your results file was
refused". `test_safe_unpickle_loads_legit_results_state` exists to catch
exactly this and is doing its job.

**Why not fixed here.** Admitting the aliases alone
(`("pandas", "DataFrame")` beside `("pandas.core.frame", "DataFrame")`) widens
nothing — same six classes — but does not make pandas 3 work, because the
pyarrow globals still refuse. Making it work means adding `pyarrow.lib`
reconstruction functions to a **security allowlist**, and whether
`_restore_array` / `py_buffer` / `type_for_alias` are acceptable there is a
judgement about the trust surface that the person who wrote the allowlist
should make, with the list above in front of them. Two things are true
regardless of that decision: the allowlist is version-fragile in a way that
will bite on any future pandas bump, and the refusal should probably say so
to the user instead of degrading to an empty tab.

### 3.2 The upload MIME sniff fails OPEN when libmagic is absent — by documented design

`routers/uploads.py::_sniff_mime`:

```python
    try:
        import magic  # python-magic
        sniffed = magic.from_buffer(blob, mime=True) or ""
    except (ImportError, OSError):
        # libmagic missing on this platform — fall through to declared MIME
```

When `python-magic` cannot be imported, the sniffer **trusts the client's
declared `Content-Type`**. The test that fails here sends a real DOS/PE
header (`MZ\x90\x00…`) named `report.xlsx` with a spreadsheet MIME declared;
with libmagic it sniffs as `application/x-dosexec` and is refused
`unsupported_mime`; without it, it is **accepted with 200**.

The code comment calls this the "magic-byte safety net" and the fall-through is
deliberate, for "a pure-pip environment without libmagic". So this is not a
bug in the sense of unintended behaviour. It is a security control whose
absence is invisible: nothing at startup, in the logs, or in the response
says the net is off. In any deployment without libmagic — which the code
itself anticipates — a renamed executable declaring an allowlisted MIME passes
validation.

**Why not fixed here.** The alternative is fail-closed (refuse uploads, or
refuse the extension-upgrade path, when libmagic is missing), which reverses
a documented design choice and may break the pip-only deployment the comment
was written for. That trade is the maintainer's. The minimum worth doing
either way is to make the degraded state **loud** — a startup warning and a
`sniff_source: "declared"` field on the response — so an operator can know.

## 4. What this changes in the PR's own claims

- "43 environmental failures" was an assertion; it is now an attribution, and
  the phrase needs qualifying: **39 environmental, 4 a latent defect masked
  by the pinned pandas**, and one of the 39 is a security control failing
  open in the environment that exposed it.
- The myopic note's "12 of 43" is 14.
- No numerical contract in the baseline is broken. The four tests named like
  contracts are the myopic issue and the unpickler.
