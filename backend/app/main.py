"""FastAPI 入口。

uvicorn app.main:app --reload --port 8000   （工作目录 backend/）
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api import align, books, export, jobs, settings, sheets

app = FastAPI(
    title="Cutter API",
    description="汉字字典切图工作台：中华字海 / 汉字海 / 康熙字典 / 汉语大字典",
    version="0.1.0",
)

if os.environ.get("CUTTER_CORS", "1") != "0":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(books.router)
app.include_router(align.router)
app.include_router(sheets.router)
app.include_router(export.router)
app.include_router(jobs.router)
app.include_router(settings.router)


@app.get("/api/health", tags=["meta"])
async def health() -> dict:
    return {
        "status": "ok",
        "data_dir": str(config.DATA_DIR),
        "data_dir_exists": config.DATA_DIR.is_dir(),
        "work_dir": str(config.WORK_DIR),
    }


FRONTEND_DIST = config.REPO_ROOT / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        index = FRONTEND_DIST / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse({"detail": "前端未构建"}, status_code=404)
else:

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {"detail": "前端未构建：先在 frontend/ 里 npm install && npm run build，或运行 npm run dev"}
