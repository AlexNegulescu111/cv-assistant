"""Runs every offline jobpilot test (no network, no browser, no API key).

Run: python -I cv-assistant/jobpilot/tests/run_tests.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
failed = []
for name in sorted(os.listdir(HERE)):
    if name.startswith("test_") and name.endswith(".py"):
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, name)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        print(f"{name}: {(r.stdout.strip().splitlines() or ['(no output)'])[-1]}")
        if r.returncode:
            failed.append(name)
            print(r.stdout + r.stderr)
sys.exit(1 if failed else 0)
