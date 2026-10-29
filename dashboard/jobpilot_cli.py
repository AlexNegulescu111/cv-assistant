"""The jobpilot CLI from this package folder, installed or not: the command the dashboard's headless Claude steps run
(`python -I <this file> <command> ...`, workspace = their cwd): those steps may only run `python -I ...`, and
`python -I -m jobpilot` fails when the package is not installed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jobpilot.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
