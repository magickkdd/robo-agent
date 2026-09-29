import glob
import os

root = "/home/czx/embodied-agent-batches/v03/e3/p1_full"
for dirpath, dirnames, filenames in os.walk(root):
    rel = os.path.relpath(dirpath, root)
    print(f"{rel}/  ->  {sorted(filenames)[:8]}")
