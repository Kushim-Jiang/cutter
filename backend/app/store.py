from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import config
from .core.box import Box

_journal_lock = threading.Lock()
_INVALID_NAME_CHARS = set('<>:"/\\|?*')

# 同一页的写必须串行：自动落盘时请求会连得很紧，两个请求同时
# 「读旧值 → 合并 state → 覆盖写」会互相吃掉对方的改动。
_locks_guard = threading.Lock()
_page_locks: dict[str, threading.Lock] = {}

# 自动落盘时快照要限流，否则拖一次框就能刷掉整段历史
_snapshot_guard = threading.Lock()
_last_snapshot: dict[str, float] = {}
SNAPSHOT_INTERVAL = 30.0


class RevisionConflict(Exception):
    """客户端基于旧版本提交（多半是另一个窗口也改了同一页）。"""

    def __init__(self, rev: int, current: dict[str, Any]) -> None:
        super().__init__("page changed elsewhere")
        self.rev = rev
        self.current = current


def _page_key(book: str, page_id: str) -> str:
    return f"{book}\x00{page_id}"


def _page_lock(book: str, page_id: str) -> threading.Lock:
    key = _page_key(book, page_id)
    with _locks_guard:
        lock = _page_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _page_locks[key] = lock
        return lock


def _atomic_write(path: Path, text: str) -> None:
    """先写唯一临时文件再 rename。共用固定 `.tmp` 名在两个写入者的
    场景下会互相截断，这是真正会丢数据的地方。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.stem + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _safe_name(value: str) -> str:
    """页号里可能有 `:`（`卷:页`），Windows 文件名不允许，统一换掉。"""
    cleaned = "".join("_" if ch in _INVALID_NAME_CHARS or ord(ch) < 32 else ch for ch in value)
    return cleaned.strip(" .") or "_"


def _history_dir(book: str, page_id: str) -> Path:
    return config.WORK_DIR / "history" / _safe_name(book) / _safe_name(page_id)[:120]


def _box_signature(boxes: list[Box]) -> str:
    return json.dumps([b.as_dict() for b in boxes], ensure_ascii=False, sort_keys=True)


def _snapshot_due(book: str, page_id: str) -> bool:
    key = _page_key(book, page_id)
    now = time.time()
    with _snapshot_guard:
        return now - _last_snapshot.get(key, 0.0) >= SNAPSHOT_INTERVAL


def _mark_snapshot(book: str, page_id: str) -> None:
    with _snapshot_guard:
        _last_snapshot[_page_key(book, page_id)] = time.time()


def _write_snapshot(
    book: str,
    page_id: str,
    boxes: list[Box],
    state: dict[str, Any],
    width: int,
    height: int,
    extra: dict[str, Any] | None,
) -> None:
    """每次保存前留一份快照（最多 200 份），供「历史编辑」查看与恢复。"""
    folder = _history_dir(book, page_id)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int((time.time() % 1) * 1000):03d}"
    payload = {
        "id": stamp,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "book": book,
        "page": page_id,
        "width": width,
        "height": height,
        "boxes": [b.as_dict() for b in boxes],
        "extra": extra or {},
        "state": state,
    }
    (folder / f"{stamp}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _mark_snapshot(book, page_id)
    snaps = sorted(folder.glob("*.json"))
    for old in snaps[:-200]:
        with contextlib.suppress(OSError):
            old.unlink()


def list_history(book: str, page_id: str) -> list[dict[str, Any]]:
    folder = _history_dir(book, page_id)
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        boxes = data.get("boxes", [])
        out.append(
            {
                "id": data.get("id", path.stem),
                "time": data.get("time", ""),
                "count": len(boxes),
                "state": data.get("state", {}),
            }
        )
    return out


def load_history(book: str, page_id: str, snapshot_id: str) -> dict[str, Any] | None:
    path = _history_dir(book, page_id) / f"{snapshot_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _page_file(book: str, page_id: str) -> Path:
    return config.BOX_DIR / _safe_name(book) / f"{_safe_name(page_id)[:120]}.ndjson"


def load_boxes(book: str, page_id: str) -> dict[str, Any] | None:
    path = _page_file(book, page_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data


def save_boxes(
    book: str,
    page_id: str,
    boxes: list[Box],
    *,
    width: int,
    height: int,
    extra: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    autosave: bool = False,
    base_rev: int | None = None,
) -> dict[str, Any]:
    """写一页的框。同一页的写按页加锁串行，写入是原子的。

    `autosave=True` 时快照限流（每页最多 30 秒一份），免得拖一次框就把
    200 份历史全刷掉；手动保存（回车 / 本页完成）永远留快照。
    `base_rev` 给出时做乐观并发校验：和磁盘上的 `rev` 不一致抛 RevisionConflict。
    """
    with _page_lock(book, page_id):
        return _save_boxes_locked(
            book,
            page_id,
            boxes,
            width=width,
            height=height,
            extra=extra,
            state=state,
            autosave=autosave,
            base_rev=base_rev,
        )


def _save_boxes_locked(
    book: str,
    page_id: str,
    boxes: list[Box],
    *,
    width: int,
    height: int,
    extra: dict[str, Any] | None,
    state: dict[str, Any] | None,
    autosave: bool,
    base_rev: int | None,
) -> dict[str, Any]:
    path = _page_file(book, page_id)
    previous = load_boxes(book, page_id) or {}
    current_rev = int(previous.get("rev", 0) or 0)
    if base_rev is not None and base_rev != current_rev:
        raise RevisionConflict(current_rev, previous)

    merged_state = {**previous.get("state", {}), **(state or {})}
    if extra is None:
        # 不传 extra = 保持原值（批量写别的页时不知道那些页的 side/pitch）
        extra = previous.get("extra", {})
    if width <= 0:
        # 切页后才落盘的请求拿不到 meta，不能把尺寸刷成 0
        width = int(previous.get("width", 0) or 0)
    if height <= 0:
        height = int(previous.get("height", 0) or 0)

    old_boxes = [Box.from_dict(b) for b in previous.get("boxes", [])]
    changed = not previous or _box_signature(old_boxes) != _box_signature(boxes)
    if changed and (not autosave or _snapshot_due(book, page_id)):
        _write_snapshot(book, page_id, boxes, merged_state, width, height, extra)

    rev = current_rev + 1
    payload = {
        "book": book,
        "page": page_id,
        "width": width,
        "height": height,
        "boxes": [b.as_dict() for b in boxes],
        "extra": extra,
        "state": merged_state,
        "rev": rev,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _atomic_write(path, json.dumps(payload, ensure_ascii=False))
    journal_append(book, page_id, len(boxes), merged_state)
    return payload


def update_state(book: str, page_id: str, state: dict[str, Any]) -> dict[str, Any]:
    """只改状态（完成标记等），同样按页加锁并递增 rev。"""
    with _page_lock(book, page_id):
        data = load_boxes(book, page_id)
        if data is None:
            data = {
                "book": book,
                "page": page_id,
                "width": 0,
                "height": 0,
                "boxes": [],
                "extra": {},
                "state": {},
                "rev": 0,
            }
        data["state"] = {**data.get("state", {}), **state}
        data["rev"] = int(data.get("rev", 0) or 0) + 1
        data["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _atomic_write(_page_file(book, page_id), json.dumps(data, ensure_ascii=False))
        journal_append(book, page_id, len(data.get("boxes", [])), data["state"])
        return data


def journal_append(book: str, page_id: str, count: int, state: dict[str, Any]) -> None:
    """追加式编辑流水（对应校对台的 edits.journal.ndjson），便于回滚与审计。"""
    record = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "book": book,
        "page": page_id,
        "count": count,
        "state": state,
    }
    with _journal_lock:
        config.WORK_DIR.mkdir(parents=True, exist_ok=True)
        with (config.WORK_DIR / "edits.ndjson").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def page_state(book: str, page_id: str) -> dict[str, Any]:
    """返回某页「是否已存框 / 是否已完成 / 是否待查」。批量任务用来跳过已处理页。"""
    data = load_boxes(book, page_id)
    if not data:
        return {"page": page_id, "saved": False, "count": 0, "done": False, "suspect": False}
    state = data.get("state", {})
    return {
        "page": page_id,
        "saved": True,
        "count": len(data.get("boxes", [])),
        "done": bool(state.get("done")),
        "suspect": bool(state.get("suspect")),
    }


class ExportLog:
    """简单追加式导出清单，便于回溯。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config.WORK_DIR / "export.ndjson")
        self._lock = threading.Lock()

    def append(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                for record in records:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")


export_log = ExportLog()
