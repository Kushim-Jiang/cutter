"""批量任务 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from .. import config, models, store
from .. import jobs as jobs_mod
from ..pipelines import registry

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
async def list_jobs() -> dict[str, Any]:
    return {"jobs": [job.as_dict() for job in jobs_mod.jobs.list()]}


@router.post("/detect")
async def create_detect_job(request: models.JobRequest) -> dict[str, Any]:
    try:
        registry.get_pipeline(request.book)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not request.pages:
        raise HTTPException(status_code=400, detail="没有选择页面")
    job = jobs_mod.run_detect_job(request.book, request.pages, request.params)
    return job.as_dict()


@router.post("/export")
async def create_export_job(request: models.JobRequest) -> dict[str, Any]:
    try:
        registry.get_pipeline(request.book)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not request.pages:
        raise HTTPException(status_code=400, detail="没有选择页面")
    job = jobs_mod.run_export_job(request.book, request.pages, request.params, request.out_dir)
    return job.as_dict()


@router.get("/{job_id}")
async def get_job(job_id: str, result: int = Query(0, ge=0, le=1)) -> dict[str, Any]:
    job = jobs_mod.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="没有这个任务")
    return job.as_dict(include_result=bool(result))


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict[str, Any]:
    job = jobs_mod.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="没有这个任务")
    if job.status in ("pending", "running"):
        job.status = "cancelled"
        job.message = "已取消"
    return job.as_dict()


@router.get("/progress/{book}")
async def book_progress(book: str) -> dict[str, Any]:
    """某书已有框的页数（按已存 sidecar 统计）。"""
    try:
        pipeline = registry.get_pipeline(book)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    pages = pipeline.pages()
    saved_pages, done_pages, suspect_pages = [], [], []
    use_seed = config.load_settings().get("use_seed", True)
    for p in pages:
        state = store.page_state(book, p["id"])
        if state["saved"]:
            saved_pages.append(p["id"])
        seeded = use_seed and pipeline.seed_counts_done() and pipeline.seed_done(p["id"])
        done = state["done"] or seeded
        if done:
            done_pages.append(p["id"])
        if state["suspect"]:
            suspect_pages.append(p["id"])
    return {
        "book": book,
        "total": len(pages),
        "saved": len(saved_pages),
        "done": len(done_pages),
        "suspect": len(suspect_pages),
        "saved_pages": saved_pages,
        "done_pages": done_pages,
        "suspect_pages": suspect_pages,
    }
