# ── PyPSA-GUI auto-push ──────────────────────────────────────────────────────
# Stages ONLY pypsa-gui/, commits any changes with a timestamped message, and
# pushes to the 'myrepo' remote (github.com/MLHaoChang/pypsa-gui). Intended to
# be run on a schedule (Windows Task Scheduler, every 3 days).
#
# Safety:
#   • No-ops when pypsa-gui/ has no changes.
#   • Lint gate: runs ruff (direct pixi-env binary) on pypsa-gui/ first; if it
#     fails, nothing is committed/pushed (don't auto-publish broken code).
#   • Scoped pathspec everywhere — never sweeps in unrelated staged files
#     (e.g. root pixi.lock) and unstages only its own changes on abort.
#   • Commits with --no-verify because the repo's pre-commit hook calls
#     `pixi run -- ruff check`, which fails to re-link the env under OneDrive
#     file locks; the direct ruff gate above replaces that check.
#   • Writes a log to pypsa-gui/scripts/auto_push.log (git-ignored via *.log).

$ErrorActionPreference = 'Continue'
$repo = 'C:\Users\HC9289\OneDrive - Hitachi Energy\Desktop\Privat\Code\Test\pypsa-eur'
$log  = Join-Path $repo 'pypsa-gui\scripts\auto_push.log'
$ruff = Join-Path $repo '.pixi\envs\default\Scripts\ruff.exe'

function Log($m) { "{0}  {1}" -f (Get-Date -Format 'o'), $m | Out-File -FilePath $log -Append -Encoding utf8 }

Set-Location -LiteralPath $repo

# 1. Stage only pypsa-gui/
git add -- pypsa-gui/ 2>&1 | Out-Null

# 2. Anything to commit?  (scoped check)
git diff --cached --quiet -- pypsa-gui/
if ($LASTEXITCODE -eq 0) { Log 'no pypsa-gui changes; nothing to push'; exit 0 }

# 3. Lint gate (direct binary — the pixi-run wrapper is unreliable on OneDrive)
if (Test-Path $ruff) {
    & $ruff check pypsa-gui/ 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Log 'ruff check FAILED on pypsa-gui/ — aborting, not committing'
        git reset -q -- pypsa-gui/ 2>&1 | Out-Null
        exit 1
    }
}

# 4. Commit (scoped) + push
$ts = Get-Date -Format 'yyyy-MM-dd HH:mm'
git commit --no-verify -m "chore(pypsa-gui): scheduled auto-push $ts" -- pypsa-gui/ 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Log 'commit failed'; exit 1 }

git push myrepo master 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Log 'push failed (network/auth?) — commit is local, will retry next run'; exit 1 }

Log "pushed scheduled auto-commit $ts"
exit 0
