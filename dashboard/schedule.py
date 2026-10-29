"""The daily auto-apply run as a Windows Task Scheduler task (runs only while the user is signed in, so toasts and
the visible browser windows work). It calls run_task.py, which hands the run to the dashboard or runs it alone.
Output of scheduled runs goes to <db folder>/dashboard_scheduled.log.
"""
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

TASK_NAME = "Jobpilot - Aplica automat"
RUN_TASK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_task.py")
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _schtasks(*args):
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def python_exe():
    """pythonw.exe when it exists: no console window flashes at the scheduled time."""
    w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return w if os.path.exists(w) else sys.executable


def get():
    """→ {supported, enabled, time "HH:MM", dry_run}"""
    if sys.platform != "win32":
        return {"supported": False, "enabled": False, "time": None, "dry_run": False}
    r = _schtasks("/Query", "/TN", TASK_NAME, "/XML")
    if r.returncode:
        return {"supported": True, "enabled": False, "time": None, "dry_run": False}
    root = ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", r.stdout))   # the declaration names UTF-16; the text is decoded
    start = root.findtext(".//t:CalendarTrigger/t:StartBoundary", default="", namespaces=NS)
    args = root.findtext(".//t:Exec/t:Arguments", default="", namespaces=NS)
    enabled = root.findtext(".//t:Settings/t:Enabled", default="true", namespaces=NS) != "false"
    m = re.search(r"T(\d\d:\d\d)", start)
    return {"supported": True, "enabled": enabled, "time": m.group(1) if m else None, "dry_run": "--dry-run" in args}


def save(ws, time, dry_run):
    """Creates or replaces the daily task. Raises ValueError / RuntimeError."""
    if sys.platform != "win32":
        raise ValueError("the daily run needs Windows Task Scheduler")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time or ""):
        raise ValueError("time as HH:MM")
    command = f'"{python_exe()}" -I "{RUN_TASK}" --workspace "{ws.root}"' + (" --dry-run" if dry_run else "")
    if len(command) > 261:
        raise ValueError("the command is longer than Task Scheduler allows (move the workspace to a shorter path)")
    r = _schtasks("/Create", "/F", "/SC", "DAILY", "/ST", time, "/TN", TASK_NAME, "/TR", command)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip())
    return get()


def delete():
    r = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    if r.returncode and "cannot find" not in (r.stderr + r.stdout).lower():
        raise RuntimeError((r.stderr or r.stdout).strip())
    return get()
