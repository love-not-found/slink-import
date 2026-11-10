import asyncio
import hmac
import logging
import os
import sys
from pathlib import Path

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, TimestampSigner
from pydantic import BaseModel, Field

from .config import ConfigError, Settings, load_settings
from .importer import (
    ImportOptions,
    SlinkClient,
    StateStore,
    format_failed_links,
    parse_collection,
    parse_links,
)
from .jobs import JobManager

STATIC = Path(__file__).parent / "static"
COOKIE = "slink_import_session"
SESSION_DAYS = 30


class LoginBody(BaseModel):
    password: str


class JobBody(BaseModel):
    links: str
    public: bool = False
    collection: str = ""
    description: str = Field("", max_length=255)
    workers: int | None = None
    force: bool = False


class RetryBody(BaseModel):
    workers: int | None = None


def check_writable(data_dir: Path) -> None:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        raise ConfigError(
            f"{data_dir} is not writable by uid {os.getuid()}: {e.strerror}. "
            "Set PUID/PGID to the owner of the data directory."
        ) from e


def create_app(settings: Settings) -> FastAPI:
    if settings.default_collection:
        parse_collection(settings.default_collection)

    check_writable(settings.data_dir)
    client = SlinkClient(settings.slink_url, settings.api_key, settings.max_image_bytes)
    state = StateStore(settings.data_dir / "state.json")
    jobs = JobManager(client, state, settings.data_dir / "jobs.json")
    signer = TimestampSigner(settings.session_secret, salt="slink-import-login")

    def logged_in(request: Request) -> bool:
        token = request.cookies.get(COOKIE, "")
        try:
            signer.unsign(token, max_age=SESSION_DAYS * 86400)
            return True
        except BadSignature:
            return False

    def require_login(request: Request) -> None:
        if not logged_in(request):
            raise HTTPException(401, "login required")

    def clamp_workers(value: int | None) -> int:
        return max(1, min(value or settings.workers, settings.max_workers))

    def options_from(body: JobBody) -> ImportOptions:
        collection_ids: tuple[str, ...] = ()
        if body.collection.strip():
            try:
                collection_ids = (parse_collection(body.collection),)
            except ValueError as e:
                raise HTTPException(422, str(e)) from None
        return ImportOptions(body.public, body.description, collection_ids)

    base = settings.base_path
    router = APIRouter(prefix=base)
    api = APIRouter(prefix=f"{base}/api", dependencies=[Depends(require_login)])

    @router.get("/", include_in_schema=False)
    def index(request: Request):
        if not logged_in(request):
            return RedirectResponse(f"{base}/login", status_code=303)
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @router.get("/login", include_in_schema=False)
    def login_page():
        return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-store"})

    @router.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True}

    @router.post("/auth/login")
    async def login(body: LoginBody, request: Request, response: Response):
        if not hmac.compare_digest(body.password.encode(), settings.ui_password.encode()):
            await asyncio.sleep(1)  # slow down password guessing
            raise HTTPException(401, "wrong password")
        response.set_cookie(
            COOKIE,
            signer.sign("ok").decode(),
            max_age=SESSION_DAYS * 86400,
            path=base or "/",
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return {"ok": True}

    @router.post("/auth/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE, path=base or "/")
        return {"ok": True}

    @api.get("/config")
    def config():
        return {
            "slinkUrl": settings.slink_url,
            "defaultPublic": settings.default_public,
            "defaultCollection": settings.default_collection,
            "workers": settings.workers,
            "maxWorkers": settings.max_workers,
        }

    @api.post("/jobs", status_code=201)
    def create_job(body: JobBody):
        links = parse_links(body.links)
        if not links:
            raise HTTPException(422, "no links given")
        return jobs.create(links, options_from(body), clamp_workers(body.workers), body.force)

    @api.get("/jobs")
    def list_jobs():
        return jobs.list()

    @api.get("/jobs/{job_id}")
    def get_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return job

    @api.post("/jobs/{job_id}/retry", status_code=201)
    def retry_job(job_id: str, body: RetryBody):
        job = jobs.get(job_id)
        failures = jobs.failures(job_id)
        if job is None or failures is None:
            raise HTTPException(404, "job not found")
        if not failures:
            raise HTTPException(422, "this job has no failed links")
        o = job["options"]
        options = ImportOptions(o["public"], o["description"], tuple(o["collectionIds"]))
        workers = clamp_workers(body.workers or job["workers"])
        return jobs.create(list(failures), options, workers, force=False)

    @api.get("/jobs/{job_id}/failed-links.txt")
    def failed_links(job_id: str):
        failures = jobs.failures(job_id)
        if failures is None:
            raise HTTPException(404, "job not found")
        return PlainTextResponse(
            format_failed_links(failures),
            headers={"Content-Disposition": 'attachment; filename="failed-links.txt"'},
        )

    app = FastAPI(title="Slink link importer", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)
    app.include_router(api)
    app.mount(f"{base}/static", StaticFiles(directory=STATIC), name="static")

    if base:
        @app.get(base, include_in_schema=False)
        def add_slash():
            return RedirectResponse(f"{base}/", status_code=308)

    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    try:
        settings = load_settings()
        app = create_app(settings)
    except (ConfigError, ValueError) as e:
        print(f"config error: {e}", file=sys.stderr)
        sys.exit(2)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.port,
        log_level="warning",
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
