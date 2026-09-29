import ast
import inspect
import textwrap

from embodied_agent.benchmark import cli

tree = ast.parse(textwrap.dedent(inspect.getsource(cli.cmd_run)))
for node in ast.walk(tree):
    if isinstance(node, ast.If) and "single" in ast.dump(node.test):
        print(ast.dump(node.test))
        print("ops:", [type(o).__name__ for o in getattr(node.test, "ops", [])])
        print("comparators:", [ast.dump(c) for c in getattr(node.test, "comparators", [])])
