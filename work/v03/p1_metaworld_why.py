"""Why does the CLI's `import metaworld` fail when a plain import in the same
interpreter succeeds?  The refusal names the interpreter to use, and `mwvenv/bin/python` is
a symlink to this very one, so the sentence may be pointing at a red herring."""
import os
import sys
import traceback

print("interpreter:", sys.executable)
print("cwd        :", os.getcwd())
print("PYTHONPATH :", os.environ.get("PYTHONPATH"))

try:
    import metaworld
    print("import metaworld OK:", metaworld.__file__)
except Exception:  # noqa: BLE001
    print("import metaworld FAILED:")
    traceback.print_exc()

# the same import the way the backend does it, after the repo is on the path
sys.path.insert(0, os.getcwd())
try:
    ml = metaworld.MT1("peg-insert-side-v3", seed=0)
    print("MT1 train_tasks:", len(ml.train_tasks), "layouts")
    print("train_classes:", list(ml.train_classes)[:5])
except Exception:  # noqa: BLE001
    print("MT1 FAILED:")
    traceback.print_exc()
