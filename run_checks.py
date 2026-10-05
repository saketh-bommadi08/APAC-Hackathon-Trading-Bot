import compileall
import subprocess
import sys

if not compileall.compile_dir(".", quiet=1):
    raise SystemExit("compile failed")

for command in ([sys.executable, "smoke_test.py"],
                [sys.executable, "test_robustness.py"]):
    result = subprocess.run(command)
    if result.returncode:
        raise SystemExit(result.returncode)

print("ALL CHECKS PASS")
