import json

d = json.load(open("configs/experiment/p3_preregistration_v1.json"))
r = d["rules"]
print("run_matrix:", json.dumps(r.get("run_matrix"), indent=1, ensure_ascii=False))
print("sampling.defaults_in_code:", json.dumps(r.get("sampling", {}).get("defaults_in_code"), indent=1, ensure_ascii=False))
print("rules keys:", sorted(r))
