"""设置 API：书源路径、导出目录、预览宽度。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .. import config, models
from . import books as books_api

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings() -> dict[str, Any]:
    settings = config.load_settings()
    settings["work_dir"] = str(config.WORK_DIR)
    settings["data_dir_exists"] = config.DATA_DIR.is_dir()
    return settings


@router.put("")
async def put_settings(patch: models.SettingsPatch) -> dict[str, Any]:
    partial = patch.model_dump(exclude_none=True)
    settings = config.save_settings(partial)
    if partial:
        from ..core import imaging

        imaging.close_all()
        books_api._meta_cache.clear()
    settings["work_dir"] = str(config.WORK_DIR)
    settings["data_dir_exists"] = config.DATA_DIR.is_dir()
    return settings
