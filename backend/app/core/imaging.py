"""PDF 取图：内嵌 1bit 扫描图、彩色扫描渲染、预览与裁切。"""

from __future__ import annotations

import io
import threading
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np

try:  # pymupdf >= 1.24 两套导入名都有
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore


class _DocCache:
    """pymupdf Document 不是线程安全的，按线程各开一份。"""

    def __init__(self, size: int = 4) -> None:
        self._size = size
        self._local = threading.local()

    def get(self, path: Path) -> fitz.Document:
        cache = getattr(self._local, "docs", None)
        if cache is None:
            cache = OrderedDict()
            self._local.docs = cache
        key = str(path)
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        while len(cache) >= self._size:
            _, old = cache.popitem(last=False)
            old.close()
        doc = fitz.open(key)
        cache[key] = doc
        return doc


_docs = _DocCache()
_ink_lock = threading.Lock()
_ink_cache: OrderedDict[tuple[str, int], np.ndarray] = OrderedDict()
_ink_limit = 4


def _store_ink(key: tuple[str, int], arr: np.ndarray) -> np.ndarray:
    global _ink_limit
    with _ink_lock:
        _ink_cache[key] = arr
        _ink_cache.move_to_end(key)
        while len(_ink_cache) > _ink_limit:
            _ink_cache.popitem(last=False)
    return arr


def embedded_ink(pdf: Path, pno: int) -> np.ndarray:
    """取页内嵌的原始 1bit 扫描图，返回 ink 掩码（1 = 墨迹），不重采样。"""
    key = (str(pdf), pno)
    with _ink_lock:
        if key in _ink_cache:
            _ink_cache.move_to_end(key)
            return _ink_cache[key]

    doc = _docs.get(Path(pdf))
    page = doc[pno - 1]
    images = page.get_images(full=True)
    if not images:
        raise ValueError(f"页 {pno} 没有内嵌图")
    info = doc.extract_image(images[0][0])
    arr = cv2.imdecode(np.frombuffer(info["image"], np.uint8), cv2.IMREAD_GRAYSCALE)
    if arr is None:
        raise ValueError(f"页 {pno} 内嵌图解不开")
    ink = (arr > 0).astype(np.uint8)
    if ink.mean() > 0.5:
        ink = 1 - ink
    return _store_ink(key, ink)


def render_gray(pdf: Path, pno: int, zoom: float = 1.5) -> np.ndarray:
    """把 PDF 页渲染成灰度数组（彩色扫描件用）。"""
    doc = _docs.get(Path(pdf))
    page = doc[pno - 1]
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    return arr.copy()


_page_lock = threading.Lock()
_page_cache: OrderedDict[tuple[str, int, int], np.ndarray] = OrderedDict()
_page_limit = 2


def render_gray_cached(pdf: Path, pno: int, zoom: float = 1.5) -> np.ndarray:
    """同上，但缓存整页灰度。300dpi 单页约 75 MB，所以只留最近两页。"""
    key = (str(pdf), pno, round(zoom * 1000))
    with _page_lock:
        hit = _page_cache.get(key)
        if hit is not None:
            _page_cache.move_to_end(key)
            return hit
    arr = render_gray(pdf, pno, zoom)
    with _page_lock:
        _page_cache[key] = arr
        _page_cache.move_to_end(key)
        while len(_page_cache) > _page_limit:
            _page_cache.popitem(last=False)
    return arr


def ink_to_gray(ink: np.ndarray) -> np.ndarray:
    return (255 - ink.astype(np.uint8) * 255).astype(np.uint8)


def gray_to_ink(gray: np.ndarray, threshold: int = 128) -> np.ndarray:
    return (gray < threshold).astype(np.uint8)


def downscale(gray: np.ndarray, max_width: int) -> np.ndarray:
    h, w = gray.shape[:2]
    if w <= max_width:
        return gray
    scale = max_width / float(w)
    return cv2.resize(gray, (max_width, max(1, round(h * scale))), interpolation=cv2.INTER_AREA)


def encode_png(gray: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", gray)
    if not ok:  # pragma: no cover
        raise RuntimeError("PNG 编码失败")
    return buf.tobytes()


def encode_jpeg(gray: np.ndarray, quality: int = 85) -> bytes:
    ok, buf = cv2.imencode(".jpg", gray, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:  # pragma: no cover
        raise RuntimeError("JPEG 编码失败")
    return buf.tobytes()


def crop(gray: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray:
    hgt, wid = gray.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(wid, x + w), min(hgt, y + h)
    if x1 <= x0 or y1 <= y0:
        return np.full((1, 1), 255, np.uint8)
    return gray[y0:y1, x0:x1]


def page_count(pdf: Path) -> int:
    return len(_docs.get(Path(pdf)))


def save_png(path: Path, gray: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", gray, [int(cv2.IMWRITE_PNG_BILEVEL), 1])
    if not ok:  # pragma: no cover
        raise RuntimeError("PNG 编码失败")
    path.write_bytes(buf.tobytes())


def to_data_uri(gray: np.ndarray) -> str:  # pragma: no cover - 调试用
    import base64

    return "data:image/png;base64," + base64.b64encode(encode_png(gray)).decode()


def open_doc(pdf: Path) -> fitz.Document:
    return _docs.get(Path(pdf))


def close_all() -> None:
    global _docs
    _docs = _DocCache()
    with _ink_lock:
        _ink_cache.clear()
    with _page_lock:
        _page_cache.clear()


def _bytes_io(data: bytes) -> io.BytesIO:
    return io.BytesIO(data)
