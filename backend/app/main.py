"""FastAPI entry point.

Run (from backend/):
    uvicorn app.main:app --reload
"""
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings, get_settings
from app.db import mongo
from app.routers import (admin_config, admin_db, alerts, auth, dashboard, devices,
                         device_data, fleet, health, local_audio, simulator, subscriptions)
from app.services.errors import ServiceError

log = logging.getLogger(__name__)


def _validation_message(exc: RequestValidationError) -> str:
    """Flatten FastAPI's list of validation errors into one readable string."""
    parts = []
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path")]
        msg = err.get("msg", "Invalid value").removeprefix("Value error, ")
        parts.append(f"{'.'.join(loc)}: {msg}" if loc else msg)
    return "; ".join(parts) or "Invalid request"


def register_error_handlers(app: FastAPI) -> None:
    """Every error response is {"detail": "<message>"} (HTTPException already is)."""

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": _validation_message(exc)})

    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(PyMongoError)
    async def _database_error(_: Request, exc: Exception) -> JSONResponse:
        log.warning("Database request failed (%s)", type(exc).__name__)
        return JSONResponse(status_code=503, content={"detail": "Database temporarily unavailable"})


# What the frontend (CORS_ORIGINS, default http://localhost:5173) may send. Authorization carries
# the JWT from POST /login; every endpoint except /health and /login needs it.
CORS_METHODS = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
CORS_HEADERS = ["Authorization", "Content-Type", "X-Device-Key"]


def _sweep_loop(stop):
    from app.services.device_service import sweep_devices
    while not stop.is_set():
        try:
            sweep_devices()
        except Exception:
            log.warning("Device sweep failed; will retry", exc_info=True)
        stop.wait(30)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if settings.MONGO_ENSURE_INDEXES_ON_STARTUP:
            try:
                mongo.ensure_indexes()
            except Exception as exc:  # noqa: BLE001 – start anyway; /admin/db/health shows Mongo as down
                log.warning("MongoDB not reachable at startup; indexes not ensured (%s)", type(exc).__name__)
        if settings.LOAD_MODEL_ON_STARTUP:
            try:
                from app.services.orchestrator import get_orchestrator
                get_orchestrator().reload_model()
            except Exception as exc:  # noqa: BLE001 – the API still serves; records fail until a model loads
                log.warning("Active model not loaded at startup (%s: %s)", type(exc).__name__, exc)
        if settings.FAIL_INTERRUPTED_JOBS_ON_STARTUP:
            try:
                from app.services.maintenance_service import fail_interrupted_jobs
                if n := fail_interrupted_jobs():
                    log.warning("Marked %d unfinished maintenance job(s) as failed (server restarted)", n)
            except Exception as exc:  # noqa: BLE001 – PostgreSQL down: /health reports it
                log.warning("Could not check unfinished maintenance jobs (%s)", type(exc).__name__)
        stop = threading.Event()
        threads = []
        if settings.PROCESSING_WORKER_ENABLED:
            from app.services.processing_worker import ProcessingWorker
            threads.append(threading.Thread(target=ProcessingWorker().run,
                                            args=(stop, settings.PROCESSING_WORKER_POLL_SECONDS),
                                            name="audio-worker", daemon=True))
        if settings.DEVICE_SWEEPER_ENABLED:
            threads.append(threading.Thread(target=_sweep_loop, args=(stop,), name="device-sweeper", daemon=True))
        for thread in threads:
            thread.start()
        try:
            yield
        finally:
            stop.set()
            for thread in threads:
                thread.join(timeout=5)

    app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=CORS_METHODS,
        allow_headers=CORS_HEADERS,
    )

    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(subscriptions.router)
    app.include_router(fleet.router)
    app.include_router(admin_config.router)
    app.include_router(admin_db.router)
    app.include_router(alerts.router)
    app.include_router(local_audio.router)
    app.include_router(devices.router)
    app.include_router(device_data.router)
    app.include_router(simulator.router)
    return app


app = create_app()
