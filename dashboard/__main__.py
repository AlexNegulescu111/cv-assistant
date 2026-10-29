"""Starts the local dashboard, or runs one dashboard task without it (what the daily schedule calls).

Run (from cv-assistant/, or with it on PYTHONPATH):
    python -m dashboard --workspace "<workspace folder>" [--port 8765 (default: $PORT, else 8765)] [--no-browser]
    python -m dashboard --workspace "<workspace folder>" --task auto_apply [--dry-run]
        → hands the task to a running dashboard (its bell and toasts report it); with no dashboard running, runs it
          here, with the same notifications, and exits when it ends
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from jobpilot.workspace import Workspace, add_argument as add_workspace_argument


def run_task(ws, kind, dry_run, port):
    body = json.dumps({"kind": kind, "dry_run": dry_run}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/tasks", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        task = json.load(urllib.request.urlopen(req, timeout=10))
        print(f"handed to the running dashboard: task {task['id']} {task['title']}")
        return 0
    except (urllib.error.URLError, OSError):
        pass
    from .notify import Notifier
    from .runner import Runner
    runner = Runner(ws)
    Notifier(ws, runner, port=port)
    task = runner.submit(kind, dry_run=dry_run)
    print(f"no dashboard running: task {task['id']} {task['title']} runs here")
    while (task := runner.get(task["id"]))["status"] in ("queued", "running"):
        time.sleep(2)
    time.sleep(1)   # the notifications are raised right after the task ends
    print(f"{task['status']}: {task['title']}")
    return 0 if task["status"] == "done" else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dashboard", description=__doc__.split("\n")[0])
    add_workspace_argument(ap)
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8765))
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--task", choices=("auto_apply", "collect", "score", "plan"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    ws = Workspace(a.workspace)
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if a.task:
        return run_task(ws, a.task, a.dry_run, a.port)

    import uvicorn
    from .app import create_app
    url = f"http://127.0.0.1:{a.port}/"
    print(f"dashboard for {ws.root} → {url}")
    if not a.no_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    app = create_app(ws, port=a.port)

    class Server(uvicorn.Server):
        def handle_exit(self, sig, frame):
            app.state.stopping.set()   # the open event streams end, so the shutdown does not wait on them
            super().handle_exit(sig, frame)

    server = Server(uvicorn.Config(app, host="127.0.0.1", port=a.port, log_level="warning",
                                   timeout_graceful_shutdown=3))
    try:
        server.run()
    except KeyboardInterrupt:   # uvicorn raises the Ctrl+C again once it has shut down cleanly
        pass
    print("dashboard oprit")


if __name__ == "__main__":
    sys.exit(main())
