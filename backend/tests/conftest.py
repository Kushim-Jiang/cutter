"""测试公共夹具：临时工作目录 + 合成 PDF。

不依赖 yedict-data（28.5 GB 的外部数据仓库），需要真实扫描件的用例用 skipif 跳过。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytest

_WORK = Path(tempfile.mkdtemp(prefix="cutter-test-"))
_DATA = Path(tempfile.mkdtemp(prefix="cutter-data-"))
os.environ.setdefault("CUTTER_WORK_DIR", str(_WORK))
os.environ.setdefault("CUTTER_DATA_DIR", str(_DATA))


def _write_bilevel_pdf(target: Path, ink: np.ndarray) -> Path:
    tmp_png = target.with_suffix(".page.png")
    gray = 255 - ink.astype(np.uint8) * 255
    cv2.imwrite(str(tmp_png), gray, [cv2.IMWRITE_PNG_BILEVEL, 1])
    h, w = ink.shape
    doc = pymupdf.open()
    page = doc.new_page(width=w, height=h)
    page.insert_image(pymupdf.Rect(0, 0, w, h), filename=str(tmp_png))
    doc.save(str(target))
    doc.close()
    tmp_png.unlink(missing_ok=True)
    return target


@pytest.fixture(scope="session")
def grid_pdf() -> Path:
    """400×600 的合成页：4 个 60×60 的字头块 + 一批 26×26 的正文字。

    汉大/康熙这类书源的版面法按「字头明显大于正文」筛选，
    所以合成页必须有正文块当基准，否则中位字高会被算成字头本身。
    """
    ink = np.zeros((600, 400), np.uint8)
    for y in (60, 260):
        for x in (60, 220):
            ink[y : y + 100, x : x + 100] = 1
    for y in (190, 400, 510):
        for x in range(20, 380, 40):
            ink[y : y + 26, x : x + 26] = 1
    path = _WORK / "grid.pdf"
    return _write_bilevel_pdf(path, ink)


@pytest.fixture(scope="session")
def client():
    from app.main import app
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture()
def work_dir() -> Path:
    return _WORK


@pytest.fixture()
def data_dir() -> Path:
    return _DATA
