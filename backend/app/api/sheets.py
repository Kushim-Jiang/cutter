"""手工对齐表接口：一列图 + 一列文本，人工配行、导出 TSV。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import sheets, store

router = APIRouter(prefix="/api/sheets", tags=["sheets"])


class CreateSheetRequest(BaseModel):
    name: str = ""
    book: str = ""
    page: str = ""
    folder: str = ""
    texts: str = ""
    tsv_path: str = ""


class RowModel(BaseModel):
    img: int | None = None
    text: str = ""
    note: str = ""


class SaveSheetRequest(BaseModel):
    rows: list[RowModel] = Field(default_factory=list)
    name: str | None = None
    base_rev: int | None = None


class ExportRequest(BaseModel):
    path: str


@router.get("")
async def index() -> dict[str, Any]:
    return {"sheets": await run_in_threadpool(sheets.list_sheets)}


@router.post("")
async def create(request: CreateSheetRequest) -> dict[str, Any]:
    try:
        return await run_in_threadpool(
            sheets.create_sheet,
            name=request.name,
            book=request.book,
            page=request.page,
            folder=request.folder,
            texts=request.texts,
            tsv_path=request.tsv_path,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{sheet_id}")
async def get_sheet(sheet_id: str) -> dict[str, Any]:
    data = await run_in_threadpool(sheets.load_sheet, sheet_id)
    if not data:
        raise HTTPException(status_code=404, detail="没有这张表")
    return data


@router.put("/{sheet_id}")
async def put_sheet(sheet_id: str, request: SaveSheetRequest) -> dict[str, Any]:
    current = await run_in_threadpool(sheets.load_sheet, sheet_id)
    if not current:
        raise HTTPException(status_code=404, detail="没有这张表")
    current["rows"] = [row.model_dump() for row in request.rows]
    if request.name is not None:
        current["name"] = request.name
    try:
        saved = await run_in_threadpool(sheets.save_sheet, current, request.base_rev)
    except store.RevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"conflict": True, "rev": exc.rev, "message": "这张表已在别处修改"},
        ) from exc
    return {"id": saved["id"], "rows": len(saved["rows"]), "rev": saved["rev"]}


@router.get("/{sheet_id}/image/{index}")
async def get_image(sheet_id: str, index: int, max_width: int = 160) -> Response:
    sheet = await run_in_threadpool(sheets.load_sheet, sheet_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="没有这张表")
    try:
        data = await run_in_threadpool(sheets.image_png, sheet, index, max_width)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(content=data, media_type="image/png")


@router.post("/{sheet_id}/export")
async def export(sheet_id: str, request: ExportRequest) -> dict[str, Any]:
    sheet = await run_in_threadpool(sheets.load_sheet, sheet_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="没有这张表")
    return await run_in_threadpool(sheets.export_tsv, sheet, request.path)
