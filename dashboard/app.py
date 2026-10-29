"""The dashboard web app: a JSON API over a jobpilot workspace plus the built React page (dashboard/static).

Local only: served on 127.0.0.1 by `python -m dashboard`. Requests must name a local Host (no DNS rebinding) and
state-changing requests must come from the page itself (Origin check), so another website open in the browser
cannot start tasks.

Shutdown (Ctrl+C): app.state.stopping is set by the server's signal handler (see __main__.py); the open event streams
end within a second, so the server closes its connections and finishes cleanly instead of waiting on them.
"""
import asyncio
import json
import os
import queue
import threading
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import data, onboarding, profile, schedule
from .mirel import Mirel, OnboardingGuide
from .notify import Notifier
from .runner import Runner

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}
KEEPALIVE_SECONDS = 15
POLL_SECONDS = 1.0   # how often an event stream checks for a disconnect or the shutdown


class TaskRequest(BaseModel):
    kind: str
    job_id: int | None = None
    dry_run: bool = False
    text: str | None = None      # "cv": the pasted job description; "competency": the user's text; "login" /
                                 # "apply_link": the URL
    source_task: int | None = None   # "apply_link": the finished CV task whose PDF is sent


class ChatRequest(BaseModel):
    text: str | None = None      # None: Mirel opens the conversation


class CredentialRequest(BaseModel):
    name: str
    value: str = ""              # empty removes the key from .env


def _host(value):
    return (urlparse(f"//{value}").hostname or "").lower() if value else ""


class LocalOnly:
    """ASGI middleware: a local Host (no DNS rebinding) on every request, a local Origin on state-changing ones.
    Plain ASGI rather than @app.middleware("http"), which wraps streaming responses and logs their cancellation at
    shutdown as "Exception in ASGI application"."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        host = _host(headers.get("host"))
        refused = None
        if host not in LOCAL_HOSTS and f"[{host}]" not in LOCAL_HOSTS:
            refused = "local requests only"
        else:
            origin = headers.get("origin")
            if scope["method"] not in ("GET", "HEAD") and origin and _host(urlparse(origin).netloc) not in LOCAL_HOSTS:
                refused = "cross-site request refused"
        if refused:
            return await JSONResponse({"detail": refused}, status_code=403)(scope, receive, send)
        await self.app(scope, receive, send)


class ApplyStateRequest(BaseModel):
    top_n: int                   # jobs per auto-apply run (best Jev score first)


class CvUpload(BaseModel):
    filename: str
    content: str                 # the file, base64 (JSON instead of multipart: no python-multipart dependency)
    extra_text: str = ""         # LinkedIn text / free description, optional


class ScheduleRequest(BaseModel):
    time: str                    # HH:MM, every day
    dry_run: bool = False


def create_app(ws, runner=None, port=8765, toaster=None):
    app = FastAPI(title="jobpilot dashboard")
    app.add_middleware(LocalOnly)
    app.state.stopping = threading.Event()
    runner = runner or Runner(ws)
    app.state.runner = runner
    mirel = Mirel(ws, runner, claude=runner.claude)
    guide = OnboardingGuide(ws, runner, claude=runner.claude)

    def written(task_id, kind, status):   # the onboarding's last Claude step finished: the step shows as done
        if kind == "onboard_write" and status == "done":
            onboarding.mark_written(ws, task_id)
    runner.before_finish.append(written)
    notifier = Notifier(ws, runner, port=port, toaster=toaster)
    app.state.notifier = notifier

    @app.get("/api/overview")
    def overview():
        return data.overview(ws)

    @app.get("/api/apply-state")
    def apply_state():
        return data.apply_state(ws)

    @app.put("/api/apply-state")
    def save_apply_state(req: ApplyStateRequest):
        try:
            profile.set_top_n(ws, req.top_n)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return data.apply_state(ws)

    @app.get("/api/jobs")
    def jobs(category: str):
        try:
            return data.jobs(ws, category)
        except KeyError:
            raise HTTPException(400, f"unknown category {category!r} (known: {', '.join(data.CATEGORIES)})")

    @app.get("/api/jobs/{job_id}")
    def job(job_id: int):
        found = data.job_detail(ws, job_id)
        if not found:
            raise HTTPException(404, f"no job {job_id}")
        return found

    @app.get("/api/jobs/{job_id}/prefill.png")
    def prefill(job_id: int):
        path = data.prefill_png(ws, job_id)
        if not path:
            raise HTTPException(404, "no prefill screenshot")
        return FileResponse(path, media_type="image/png")

    @app.get("/api/file")
    def file(path: str):
        """A CV made by a task (PDF/DOCX under the workspace's pdf / docx folders only)."""
        found = data.cv_file(ws, path)
        if not found:
            raise HTTPException(404, "no such CV file")
        return FileResponse(found, filename=os.path.basename(found))

    @app.get("/api/usage")
    def usage(days: int = 30):
        return data.usage(ws, max(1, min(days, 90)))

    @app.get("/api/skills")
    def skills():
        return data.skills_to_learn(ws)

    @app.get("/api/credentials")
    def credentials():
        return data.credentials(ws)

    @app.post("/api/credentials")
    def set_credential(req: CredentialRequest):
        try:
            return profile.set_credential(ws, req.name, req.value)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/profile")
    def get_profile():
        return profile.get(ws)

    @app.put("/api/profile")
    def save_profile(changes: dict):
        try:
            return profile.save(ws, changes)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/tasks/{task_id}/revert")
    def revert(task_id: int):
        try:
            return {"message": runner.revert(task_id)}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/onboarding")
    def onboarding_state():
        return onboarding.state(ws)

    @app.post("/api/onboarding/cv")
    def onboarding_cv(req: CvUpload):
        try:
            onboarding.upload_cv(ws, req.filename, req.content, req.extra_text)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return onboarding.state(ws)

    @app.put("/api/onboarding/facts")
    def onboarding_facts(facts: dict):
        try:
            return onboarding.save_facts(ws, facts)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.put("/api/onboarding/answers")
    def onboarding_answers(answers: dict):
        try:
            return onboarding.save_answers(ws, answers)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/onboarding/skip-questions")
    def onboarding_skip():
        return onboarding.skip_enrichment(ws)

    @app.get("/api/guide")
    def guide_state():
        return guide.state()

    @app.post("/api/guide")
    def guide_send(req: ChatRequest):
        try:
            guide.send(req.text)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @app.post("/api/guide/reset")
    def guide_reset():
        try:
            guide.reset()
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @app.get("/api/mirel")
    def mirel_state():
        return mirel.state()

    @app.post("/api/mirel")
    def mirel_send(req: ChatRequest):
        try:
            mirel.send(req.text)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @app.post("/api/mirel/reset")
    def mirel_reset():
        try:
            mirel.reset()
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @app.get("/api/notifications")
    def notifications():
        return notifier.list()

    @app.post("/api/notifications/read")
    def notifications_read(body: dict):
        notifier.mark_read([int(i) for i in body.get("ids") or []])   # no ids: all
        return notifier.list()

    @app.put("/api/notifications/settings")
    def notifications_settings(settings: dict):
        notifier.set_toasts(bool(settings.get("toasts")))
        return notifier.list()

    @app.get("/api/schedule")
    def get_schedule():
        return schedule.get()

    @app.put("/api/schedule")
    def save_schedule(req: ScheduleRequest):
        try:
            return schedule.save(ws, req.time, req.dry_run)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/schedule")
    def delete_schedule():
        try:
            return schedule.delete()
        except RuntimeError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/workspace")
    def workspace():
        return {"root": ws.root}

    @app.get("/api/tasks")
    def tasks():
        return runner.list()

    @app.post("/api/tasks")
    def start_task(req: TaskRequest):
        if req.kind == "resume":
            job = data.job_detail(ws, req.job_id) if req.job_id is not None else None
            if not job or job["status"] in ("applied", "skipped", "filtered_out", "duplicate"):
                raise HTTPException(400, "resume needs an open job")
        try:
            return runner.submit(req.kind, req.job_id, req.dry_run, req.text, req.source_task)
        except KeyError:
            raise HTTPException(400, f"unknown task kind {req.kind!r}")
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/tasks/{task_id}")
    def task(task_id: int):
        found = runner.get(task_id, log=True)
        if not found:
            raise HTTPException(404, f"no task {task_id}")
        return found

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel(task_id: int):
        found = runner.cancel(task_id)
        if not found:
            raise HTTPException(404, f"no task {task_id}")
        return found

    @app.get("/api/events")
    async def events(request: Request):
        """Server-sent events: task changes and log lines, as they happen. Ends when the page goes away or the
        server stops."""
        listener = runner.subscribe()
        stopping = app.state.stopping

        async def stream():
            try:
                yield "retry: 3000\n\n"
                idle = 0.0
                while not stopping.is_set():
                    try:
                        event = await asyncio.to_thread(listener.get, True, POLL_SECONDS)
                    except queue.Empty:
                        if await request.is_disconnected():
                            break
                        idle += POLL_SECONDS
                        if idle >= KEEPALIVE_SECONDS:
                            idle = 0.0
                            yield ": keepalive\n\n"
                        continue
                    idle = 0.0
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                runner.unsubscribe(listener)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    if os.path.isdir(STATIC):
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
