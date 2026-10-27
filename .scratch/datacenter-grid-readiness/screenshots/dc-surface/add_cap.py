import glob, os, yaml
p = glob.glob(os.path.expanduser("~/Documents/PyPSA Studio/Projects/*/dcsurf-demo/campus_electrical/campus_input.yaml"))[0]
d = yaml.safe_load(open(p))
d["campus"]["compensation"] = [{"name": "MV_CAP", "kind": "capacitor_bank", "bus": "DC_MV",
                                "q_mvar": {"value": 12.0, "source": "assumed"}, "steps": 2}]
yaml.safe_dump(d, open(p, "w"), sort_keys=False)
print("ok", p[-60:])
