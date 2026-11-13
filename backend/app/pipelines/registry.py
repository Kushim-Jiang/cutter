"""书源注册表。"""

from __future__ import annotations

from typing import Any

from .. import config
from .base import Pipeline
from .hd import HdPipeline
from .hzh import HzhPipeline
from .kx import KxPipeline
from .zihai import ZihaiPipeline

_CLASSES: dict[str, type[Pipeline]] = {
    "zihai": ZihaiPipeline,
    "hzh": HzhPipeline,
    "kx": KxPipeline,
    "hd": HdPipeline,
}

ORDER = ["zihai", "hzh", "kx", "hd"]


def get_pipeline(book_id: str) -> Pipeline:
    cls = _CLASSES.get(book_id)
    if cls is None:
        raise KeyError(f"未知书源：{book_id}")
    return cls(config.book_setting(book_id))


def all_pipelines() -> list[Pipeline]:
    return [get_pipeline(book) for book in ORDER]


def describe(pipeline: Pipeline) -> dict[str, Any]:
    return {
        "id": pipeline.id,
        "name": pipeline.name,
        "description": pipeline.description,
        "status": pipeline.status(),
        "params": pipeline.params_spec(),
        "defaults": pipeline.defaults(),
    }
