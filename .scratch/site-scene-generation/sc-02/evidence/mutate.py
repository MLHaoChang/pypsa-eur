"""SC-02a mutation checks: each mutation alone, both SC-02a test files run, then restored."""
import pathlib, subprocess, sys
A = "services/site_scene/associations.py"
P = "services/site_scene/proposals.py"
S = "services/site_service.py"
M = [
 ("M1 read-back resolves by name (uid ignored)", A,
  'name = component_identity.name_of(n, a["class"], a["uid"])',
  'name = a.get("name_at_bind") if _exists(n, a["class"], a.get("name_at_bind") or "") else None'),
 ("M2 Apply stores no uid", A, '"uid": uid, "name_at_bind": new["expect"]["name"]',
  '"uid": "name:" + new["expect"]["name"], "name_at_bind": new["expect"]["name"]'),
 ("M3 no cycle check", A, "if state.get(child) == 1:", "if False:"),
 ("M4 no allocation sum check", A, "if abs(sum(given) - 1.0) > WEIGHT_TOLERANCE:", "if False:"),
 ("M5 Apply does not re-validate (GK B condition 9)", A,
  'if not _exists(n, cls, name):\n        raise _changed(f"{label} is no longer in the model: review again")\n'
  '    if attachable:',
  'if False:\n        pass\n    if False:'),
 ("M5c Apply skips the bus/carrier re-check", A,
  'if (now["bus"], now["carrier"]) != (expect["bus"], expect["carrier"]):', "if False:"),
 ("M5b Apply skips the instance (uid) check", A, 'if now["uid"] != expect["uid"] and not', "if False and not"),
 ("M6 legacy delete hook writes without the scene mutex", S, "with scene_store.project_lock(pdir):",
  'with __import__("contextlib").nullcontext():'),
 ("M7 place_existing ignores a stale binding", A,
  "return isinstance(recorded_uid, str) and component_identity.get(n, cls, name) != recorded_uid", "return False"),
 ("M8 rebind skips the allocation check", A, "    _check_allocation(_rows_of(n, after))\n", "    pass\n"),
 ("M9 double count ignores the Load's carrier", A, 'if _electric(_carrier(n, "Load", load["name"])):', "if True:"),
 ("M10 Apply takes the scene mutex without the network lock", P,
  "with PyPSAService.get_lock(), store.project_lock(project_dir):", "with store.project_lock(project_dir):"),
]
TESTS = ["tests/test_site_scene_associations.py", "tests/test_site_scene_placement.py"]
only = set(sys.argv[1:])
for name, path, old, new in M:
    if only and name.split()[0] not in only:
        continue
    f = pathlib.Path(path); src = f.read_text()
    assert src.count(old) >= 1, name
    f.write_text(src.replace(old, new, 1))
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *TESTS],
                           capture_output=True, text=True)
    finally:
        f.write_text(src)
    print(name); print("  exit", r.returncode)
    for line in r.stdout.splitlines():
        if line.startswith(("FAILED", "ERROR")):
            print("  " + line.split(" - ")[0])
    sys.stdout.flush()
r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *TESTS], capture_output=True)
print("Restored: exit", r.returncode)
