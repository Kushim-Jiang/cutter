"""API 请求/响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class BoxModel(BaseModel):
    x: int
    y: int
    w: int = Field(gt=0)
    h: int = Field(gt=0)
    selected: bool = True
    source: str = "manual"
    band: int | None = None
    line: int | None = None
    col: int | None = None
    seq: int | None = None
    pos: str | None = None
    pitch: float | None = None
    cw: float | None = None

    @field_validator("x", "y", "w", "h", "band", "line", "col", "seq", mode="before")
    @classmethod
    def _round_coord(cls, value: Any) -> Any:
        """前端画布算出来的坐标常带小数（吸附/缩放后），四舍五入而不是 422。"""
        if isinstance(value, float):
            return round(value)
        return value


class SaveBoxesRequest(BaseModel):
    boxes: list[BoxModel]
    width: int = 0
    height: int = 0
    extra: dict[str, Any] | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    # 自动落盘：快照限流（手动保存永远留快照）
    autosave: bool = False
    # 客户端手上这页的版本号；对不上说明别处也改了（409）
    base_rev: int | None = None


class RefineRequest(BaseModel):
    boxes: list[BoxModel]
    params: dict[str, Any] = Field(default_factory=dict)
    save: bool = False


class StateRequest(BaseModel):
    done: bool | None = None
    suspect: bool | None = None


class DetectRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    save: bool = False


class ExportRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    out_dir: str | None = None


class JobRequest(BaseModel):
    book: str
    pages: list[str]
    params: dict[str, Any] = Field(default_factory=dict)
    out_dir: str | None = None


class SettingsPatch(BaseModel):
    preview_width: int | None = None
    export_dir: str | None = None
    gap: int | None = None
    use_seed: bool | None = None
    books: dict[str, dict[str, Any]] | None = None
