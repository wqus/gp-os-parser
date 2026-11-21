import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from gp_os.client import SiteClient
from gp_os.collector import AlreadyRunning, Collector, load_records
from gp_os.sources import SOURCES
from gp_os.storage import Store

logger = logging.getLogger(__name__)


def next_run_at(now: datetime, at: str, timezone: str) -> datetime:
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone)
    target = datetime.combine(local.date(), time.fromisoformat(at), zone)
    if target <= local:
        target += timedelta(days=1)
    return target.astimezone(UTC)


def create_app(
    *, data_dir: Path | None = None, seed: Path | None = None, schedule_enabled: bool | None = None
) -> FastAPI:
    timezone = os.getenv("GP_OS_TIMEZONE", "Europe/Moscow")
    run_at = os.getenv("GP_OS_RUN_AT", "03:00")
    enabled = (
        schedule_enabled
        if schedule_enabled is not None
        else os.getenv("GP_OS_SCHEDULE_ENABLED", "true").lower() == "true"
    )
    next_run_at(datetime.now(UTC), run_at, timezone)

    async def collect(application):
        application.state.last_error = None
        try:
            config_path = Path(os.getenv("GP_OS_SOURCES", "sources.json"))
            config = (
                json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
            )
            async with SiteClient() as client:
                await Collector(
                    application.state.store, client, timezone=timezone, config=config
                ).run()
        except AlreadyRunning as error:
            application.state.last_error = str(error)
        except Exception as error:
            logger.exception("Collection failed")
            application.state.last_error = f"{type(error).__name__}: {error}"

    async def schedule(application):
        while True:
            target = next_run_at(datetime.now(UTC), run_at, timezone)
            await asyncio.sleep(max(0, (target - datetime.now(UTC)).total_seconds()))
            if not application.state.job or application.state.job.done():
                application.state.job = asyncio.create_task(collect(application))

    @asynccontextmanager
    async def lifespan(application):
        store = Store(data_dir or Path(os.getenv("GP_OS_DATA_DIR", "data")))
        seed_path = seed or Path("samples/gp_os.json")
        if seed_path.exists():
            store.seed(load_records(seed_path))
        application.state.store = store
        application.state.job = None
        application.state.last_error = None
        timer = asyncio.create_task(schedule(application)) if enabled else None
        try:
            yield
        finally:
            for task in (timer, application.state.job):
                if task:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    app = FastAPI(title="GP OS Collector", version="0.1.0", lifespan=lifespan)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        return HTMLResponse(
            (Path(__file__).parent / "static/index.html").read_text(encoding="utf-8")
        )

    @app.get("/api/status")
    async def status():
        store = app.state.store
        runs = store.runs()
        records = store.records()
        return {
            "running": bool(app.state.job and not app.state.job.done()) or store.is_running(),
            "last_run": runs[0] if runs else None,
            "runs": runs,
            "last_error": app.state.last_error,
            "next_run_at": next_run_at(datetime.now(UTC), run_at, timezone).isoformat()
            if enabled
            else None,
            "timezone": timezone,
            "total": len(records),
            "sources": {
                source: {
                    "name": spec.name,
                    "count": sum(record.source == source for record in records.values()),
                }
                for source, spec in SOURCES.items()
            },
        }

    @app.get("/api/records")
    async def records():
        return {
            key: value.model_dump(mode="json") for key, value in app.state.store.records().items()
        }

    @app.post("/api/run", status_code=202)
    async def run():
        if (app.state.job and not app.state.job.done()) or app.state.store.is_running():
            raise HTTPException(409, "A collection is already in progress")
        app.state.job = asyncio.create_task(collect(app))
        return {"status": "started"}

    @app.get("/download")
    async def download():
        return FileResponse(
            app.state.store.export(),
            filename="gp_os.json",
            media_type="application/json; charset=utf-8",
        )

    return app
