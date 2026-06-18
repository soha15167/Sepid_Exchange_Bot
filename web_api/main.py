from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import WEB_API_PORT, WEB_FRONTEND_URL
from database.db import ensure_schema
from web_api.routers import admin, adverts, auth, offers

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    ensure_schema()
    app = FastAPI(
        title="Sepid Exchange Web API",
        version="1.0.0",
        description="مکمل وب ربات Sepid Exchange",
    )
    origins = [
        WEB_FRONTEND_URL,
        "http://localhost:3100",
        "http://127.0.0.1:3100",
        "http://49.13.132.230:3100",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth.router, prefix="/api")
    app.include_router(adverts.router, prefix="/api")
    app.include_router(offers.router, prefix="/api")
    app.include_router(admin.router, prefix="/api")
    from web_api.routers import info as info_router

    app.include_router(info_router.router, prefix="/api")

    @app.get("/api/health")
    def health():
        return {"ok": True, "port": WEB_API_PORT}

    return app


app = create_app()
