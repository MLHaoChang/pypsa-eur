"""
Installs sitecustomize.py into the active pixi environment's site-packages
to globally disable SSL certificate verification.

Run with:  pixi run setup-ssl-bypass
"""
import shutil
import site
import sys
from pathlib import Path

CONTENT = '''\
"""SSL verification bypass — installed by scripts/install_ssl_bypass.py"""
import os, ssl

os.environ.setdefault("PYTHONHTTPSVERIFY", "0")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "")
os.environ.setdefault("CURL_CA_BUNDLE", "")

def _unverified_context(*args, **kwargs):
    ctx = ssl._create_unverified_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

ssl._create_default_https_context = _unverified_context
ssl.create_default_context = _unverified_context

def _patch_urllib3():
    import urllib3, urllib3.util.ssl_ as _ssl
    urllib3.disable_warnings()
    _orig = _ssl.create_urllib3_context
    def _no_verify(*a, **kw):
        ctx = _orig(*a, **kw); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE; return ctx
    _ssl.create_urllib3_context = _no_verify
    try:
        import requests.packages.urllib3.util.ssl_ as _r; _r.create_urllib3_context = _no_verify
    except Exception: pass

def _patch_requests():
    import requests
    _orig = requests.Session.request
    def _no_verify(self, method, url, **kw): kw.setdefault("verify", False); return _orig(self, method, url, **kw)
    requests.Session.request = _no_verify

try: _patch_urllib3()
except Exception: pass
try: _patch_requests()
except Exception: pass
'''

pkgs = site.getsitepackages()
# Prefer the Lib/site-packages entry on Windows
target_dir = next((Path(p) for p in pkgs if "site-packages" in p), Path(pkgs[0]))
target = target_dir / "sitecustomize.py"

target.write_text(CONTENT, encoding="utf-8")
print(f"SSL bypass installed → {target}")
print("All httpx / urllib3 / requests calls will skip certificate verification.")

# ── Fix Snakemake's unquoted Python executable path (Windows spaces in path) ─
import snakemake.script as _smscript
script_init = Path(_smscript.__file__)
old = '"{py_exec} {fname:q}"'
new = '"{py_exec:q} {fname:q}"'
text = script_init.read_text(encoding="utf-8")
if old in text:
    script_init.write_text(text.replace(old, new), encoding="utf-8")
    print(f"Snakemake Windows path fix applied → {script_init}")
elif new in text:
    print(f"Snakemake Windows path fix already present.")
else:
    print(f"WARNING: Could not find expected string in {script_init} — manual check needed.")
