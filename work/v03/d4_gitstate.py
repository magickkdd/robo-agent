import json

from embodied_agent.core.events import git_state

s = git_state()
print("commit           :", s["commit"])
print("dirty            :", s["dirty"])
print("dirty_diff_sha256:", s["dirty_diff_sha256"], " <- v0.2 published b43d31f369f688d3")
print("changed_files    :", len(s["changed_files"]))
print("untracked_code   :", json.dumps(s["untracked_code"], sort_keys=True))
print("dirty_diff_stat  :")
print(s["dirty_diff_stat"])
