"""本页字框的合法性检查。

通用项（所有书源）：
  * 出界 / 过小 / 空白框（框里几乎没有墨迹）
  * 重复框（位置几乎一样）与重叠框（IoU 偏高）
  * 尺寸离群（比本页中位大 1.35 倍或小 0.6 倍）

汉字海专项（用上游 _zihai/hh 的 OCR 字段做「编号检查」）：
  * 条目号缺失 / 重复 / 不递增（缺号就是疑似漏切）
  * 部首、笔画、码位缺失或格式不对
  * 字段条数与本页框数不一致（说明框被增删过，号码需要重扫）

上游字段文件：
  _zihai/hh/fields.ndjson      每条 {page, seq, col, x, y, w, h, entry, radical, strokes, code, conf}
  _zihai/hh/fields_fix.ndjson  人工修正，按 (page, seq) 覆盖前者
"""

from __future__ import annotations

import json
import re
import threading
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

from . import config
from .core.box import Box, coverage_ratio, iou

_CODE_RE = re.compile(r"^[0-9A-Fa-f]{4,6}$")
_FIELDS_LOCK = threading.Lock()
_FIELDS_CACHE: dict[str, dict[int, list[dict[str, Any]]]] = {}

HZH_FIELDS = "fields"
HZH_ENTRY_MAX = 102434


def issue(
    kind: str, level: str, message: str, boxes: list[int] | None = None, detail: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"kind": kind, "level": level, "message": message, "boxes": boxes or [], "detail": detail or {}}


# ── 汉字海字段 ────────────────────────────────────────────
def _hzh_fields_dir() -> Path:
    return config.DATA_DIR / "_zihai" / "hh"


def load_hzh_fields() -> dict[int, list[dict[str, Any]]]:
    """按页索引的上游 OCR 字段（fields_fix 覆盖 fields）。进程内缓存。"""
    with _FIELDS_LOCK:
        cached = _FIELDS_CACHE.get(HZH_FIELDS)
        if cached is not None:
            return cached

        by_page: dict[int, dict[int, dict[str, Any]]] = {}
        base = _hzh_fields_dir()
        for name, override in (("fields.ndjson", False), ("fields_fix.ndjson", True)):
            path = base / name
            if not path.is_file():
                continue
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    page = int(row.get("page") or 0)
                    seq = int(row.get("seq") or 0)
                    if not page or not seq:
                        continue
                    # 同一 (page, seq) 时，fields_fix 覆盖 fields
                    by_page.setdefault(page, {})[seq] = (
                        row if not override else {**by_page.get(page, {}).get(seq, {}), **row}
                    )

        result = {page: [rows[k] for k in sorted(rows)] for page, rows in by_page.items()}
        _FIELDS_CACHE[HZH_FIELDS] = result
        return result


def _map_fields_to_boxes(rows: list[dict[str, Any]], boxes: list[Box]) -> list[dict[str, Any] | None]:
    """把字段行按位置对到当前框上：优先用序号，序号对不上时按中心距离。"""
    mapping: list[dict[str, Any] | None] = [None] * len(boxes)
    used: set[int] = set()
    for row in rows:
        seq = int(row.get("seq") or 0) - 1
        if 0 <= seq < len(boxes) and seq not in used:
            mapping[seq] = row
            used.add(seq)
    for row in rows:
        if any(row is m for m in mapping):
            continue
        cx = float(row.get("x") or 0) + float(row.get("w") or 0) / 2
        cy = float(row.get("y") or 0) + float(row.get("h") or 0) / 2
        size = max(float(row.get("w") or 1), float(row.get("h") or 1), 1)
        best, best_d = -1, None
        for i, box in enumerate(boxes):
            if mapping[i] is not None:
                continue
            d = ((box.x + box.w / 2 - cx) ** 2 + (box.y + box.h / 2 - cy) ** 2) ** 0.5
            if d < size * 0.6 and (best_d is None or d < best_d):
                best, best_d = i, d
        if best >= 0:
            mapping[best] = row
    return mapping


def check_hzh_numbering(page_id: str, boxes: list[Box]) -> list[dict[str, Any]]:
    try:
        page = int(page_id)
    except (TypeError, ValueError):
        return [issue("numbering-unavailable", "warn", "页号不是数字，跳过编号检查")]
    fields = load_hzh_fields()
    rows = fields.get(page)
    if not rows:
        return [
            issue("numbering-unavailable", "warn", "上游没有这一页的编号字段（_zihai/hh/fields*.ndjson）")
        ]

    out: list[dict[str, Any]] = []
    mapping = _map_fields_to_boxes(rows, boxes)
    missing = [i for i, m in enumerate(mapping) if m is None]

    if len(rows) != len(boxes):
        out.append(
            issue(
                "numbering-count",
                "warn",
                f"字段条数 {len(rows)} 与本页框数 {len(boxes)} 不一致；增删过框就要重扫编号",
                boxes=missing,
            )
        )
    elif missing:
        out.append(
            issue(
                "numbering-mismatch",
                "warn",
                f"{len(missing)} 个框对不上编号字段（位置变化太大）",
                boxes=missing,
            )
        )

    entries = []
    for i, row in enumerate(mapping):
        if row is None:
            continue
        entry = int(row.get("entry") or 0)
        entries.append((i, entry))
        if entry <= 0:
            out.append(
                issue("entry-missing", "warn", "条目号没读出来", boxes=[i], detail={"seq": row.get("seq")})
            )
        elif entry > HZH_ENTRY_MAX:
            out.append(issue("entry-range", "error", f"条目号 {entry} 超出 1–{HZH_ENTRY_MAX}", boxes=[i]))

        radical = (row.get("radical") or "").strip()
        if not radical:
            out.append(issue("radical-missing", "warn", "部首为空", boxes=[i]))
        elif len(radical) > 3:
            out.append(issue("radical-odd", "warn", f"部首「{radical}」看起来不止一个字", boxes=[i]))

        strokes = row.get("strokes")
        try:
            strokes_i = int(strokes) if strokes is not None else 0
            if not 1 <= strokes_i <= 64:
                out.append(issue("strokes-range", "warn", f"笔画 {strokes_i} 不在 1–64", boxes=[i]))
        except (TypeError, ValueError):
            out.append(issue("strokes-missing", "warn", "笔画缺失或不是数字", boxes=[i]))

        code = (row.get("code") or "").strip()
        if not code:
            out.append(issue("code-missing", "warn", "码位为空", boxes=[i]))
        elif not _CODE_RE.match(code):
            out.append(issue("code-format", "warn", f"码位「{code}」不是 4–6 位十六进制", boxes=[i]))
        elif len(code) >= 4:
            try:
                ch = chr(int(code, 16))
                if not ch.isprintable():
                    raise ValueError
            except (ValueError, OverflowError):
                out.append(issue("code-invalid", "error", f"码位 {code} 不是合法字符", boxes=[i]))

    # 条目号连续性：按框顺序应严格递增
    ordered_entries = sorted(entries)
    seen: dict[int, int] = {}
    for box_i, entry in sorted(entries):
        if entry <= 0:
            continue
        if entry in seen:
            out.append(
                issue(
                    "entry-duplicate",
                    "error",
                    f"条目号 {entry} 重复（第 {seen[entry] + 1} 与第 {box_i + 1} 个框）",
                    boxes=[seen[entry], box_i],
                )
            )
        else:
            seen[entry] = box_i
    for (_, prev), (box_i, cur) in pairwise(ordered_entries):
        if prev > 0 and cur > 0 and cur != prev + 1:
            if cur < prev:
                out.append(issue("entry-order", "error", f"条目号倒序：{prev} → {cur}", boxes=[box_i]))
            else:
                out.append(
                    issue(
                        "entry-gap",
                        "warn",
                        f"条目号跳号：{prev} → {cur}（缺 {cur - prev - 1} 个，疑似漏切）",
                        boxes=[box_i],
                        detail={"from": prev, "to": cur},
                    )
                )
    return out


# ── 通用检查 ──────────────────────────────────────────────
def check_geometry(
    boxes: list[Box], page_width: int, page_height: int, ink: np.ndarray | None = None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not boxes:
        return [issue("empty-page", "warn", "本页还没有任何框")]

    sizes = [max(b.w, b.h) for b in boxes]
    med = float(np.median(sizes)) or 1.0

    for i, box in enumerate(boxes):
        if box.x < 0 or box.y < 0 or box.x + box.w > page_width or box.y + box.h > page_height:
            out.append(
                issue(
                    "out-of-bounds", "warn", f"框 {i + 1} 超出页面（{page_width}×{page_height}）", boxes=[i]
                )
            )
        if box.w < 20 or box.h < 20:
            out.append(issue("too-small", "warn", f"框 {i + 1} 只有 {box.w}×{box.h}", boxes=[i]))
        m = max(box.w, box.h)
        if m > 1.35 * med:
            out.append(issue("size-large", "warn", f"框 {i + 1} 偏大（{box.w}×{box.h}）", boxes=[i]))
        elif m < 0.6 * med:
            out.append(issue("size-small", "warn", f"框 {i + 1} 偏小（{box.w}×{box.h}）", boxes=[i]))
        if ink is not None:
            patch = ink[max(0, box.y) : box.y + box.h, max(0, box.x) : box.x + box.w]
            if patch.size and patch.mean() < 0.005:
                out.append(issue("blank-box", "error", f"框 {i + 1} 里几乎没有墨迹", boxes=[i]))

    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            if coverage_ratio(a, b) > 0.9 or coverage_ratio(b, a) > 0.9:
                out.append(issue("duplicate", "error", f"框 {i + 1} 与 {j + 1} 几乎重合", boxes=[i, j]))
            elif iou(a, b) > 0.35:
                out.append(
                    issue("overlap", "warn", f"框 {i + 1} 与 {j + 1} 重叠 {iou(a, b):.0%}", boxes=[i, j])
                )
    return out


def check_page(
    book_id: str,
    pipeline,
    page_id: str,
    boxes: list[Box],
    page_size: tuple[int, int],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    width, height = page_size
    try:
        ink = pipeline.ink(page_id)
    except Exception:
        ink = None

    issues = check_geometry(boxes, width, height, ink)

    if book_id == "hzh":
        issues.extend(check_hzh_numbering(page_id, boxes))

    if extra and extra.get("suspects"):
        boxes_hint = [s.get("col") for s in extra["suspects"] if isinstance(s, dict)]
        issues.append(
            issue(
                "detected-suspect",
                "warn",
                f"检测时有 {len(extra['suspects'])} 处疑似漏切（有属性行但没有字头框）",
                detail={"cols": boxes_hint},
            )
        )

    errors = sum(1 for i in issues if i["level"] == "error")
    warns = sum(1 for i in issues if i["level"] == "warn")
    return {
        "book": book_id,
        "page": page_id,
        "issues": issues,
        "stats": {"total": len(issues), "errors": errors, "warnings": warns, "boxes": len(boxes)},
    }
