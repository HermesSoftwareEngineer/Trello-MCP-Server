"""Roda toda a suite. Uso: .venv\\Scripts\\python.exe tests/run_all.py"""

import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
SUITES = ["test_auth.py", "test_tools.py", "test_mcp.py"]


def main() -> int:
    failures = []
    for suite in SUITES:
        print(f"\n{'#' * 60}\n# {suite}\n{'#' * 60}")
        completed = subprocess.run([sys.executable, str(HERE / suite)])
        if completed.returncode != 0:
            failures.append(suite)

    print(f"\n{'=' * 60}")
    if failures:
        print("SUITES COM FALHA: " + ", ".join(failures))
        return 1
    print("TODAS AS SUITES PASSARAM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
