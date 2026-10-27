"""Plan-card states as the backend's own _view writes them (LC-02 shots)."""
import json, sys
from services.assistant_tasks import _view

def step(i, tool, status, args=None, **kw):
    return {"id": i, "tool": tool, "status": status, "args": args or {}, **kw}

def receipt(says):
    return {"_edit_receipt": {"says": says}}

base = {"project_id": "oj-grid"}
tid = lambda n: f"0ba037fe-bc46-43d3-a9c2-9b587c9cd5{n:02d}"
views = {}
views["next"] = _view({**base, "task_id": tid(1), "title": "Raise L1 and re-solve", "status": "ready", "steps": [
    step("raise", "set_component_attribute", "completed", result=receipt("Set L1 s_nom 300 → 500 MVA")),
    step("solve", "solve_network", "pending", {"mode": "lopf"}),
    step("chart", "create_chart", "pending", {"kind": "dispatch"})]})
views["waiting"] = _view({**base, "task_id": tid(2), "title": "Raise L1 and re-solve", "status": "waiting_job", "steps": [
    step("raise", "set_component_attribute", "completed", result=receipt("Set L1 s_nom 300 → 500 MVA")),
    step("solve", "solve_network", "waiting", {"mode": "lopf"}, job={"job_id": "job-7f3c21"}),
    step("chart", "create_chart", "pending", {"kind": "dispatch"})]})
views["brief"] = _view({**base, "task_id": tid(3), "title": "Battery sizing study", "status": "ready",
    "brief": {"objective": "Size the battery for the evening peak", "cards": ["capacity", "grid", "hand-off"],
              "cases": [{"name": "Base"}],
              "phases": [{"card": "capacity", "steps": ["cap"]}, {"card": "grid", "steps": ["lf", "check"]},
                         {"card": "hand-off", "steps": ["report"]}]},
    "steps": [step("cap", "solve_network", "completed"), step("lf", "run_load_flow", "pending", {"snapshot": 18}),
              step("check", "check_grid_code", "pending"), step("report", "generate_report", "pending")]})
long = [step(f"edit{i}", "set_component_attribute", "completed",
             result=receipt(f"Set Battery {i} max hours 4 → 6 h; capital cost 120 → 150 €/kW"
                            f" (unit {i} of 11)"))
        for i in range(1, 12)]
views["trimmed"] = _view({**base, "task_id": tid(4), "title": "Retune eleven batteries and re-solve", "status": "ready",
    "steps": long + [step("solve", "solve_network", "pending", {"mode": "lopf", "note": "x" * 1700})]})
views["review"] = _view({**base, "task_id": tid(5), "title": "Import tariffs and re-solve", "status": "needs_review", "steps": [
    step("load", "read_load_profile", "failed", error="profile column 'kW' not found", retries=1),
    step("tariff", "set_component_attribute", "uncertain", {"name": "G1"}, error="interrupted; effects may exist"),
    step("solve", "solve_network", "pending")]})
json.dump(views, sys.stdout)
