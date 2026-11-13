"""跨来源对齐表：一列字形/文本 + 每卷切图，按页内位置号逐行对齐。

移植自 `yedict-data/_zihai/table/build_table.py`，但**不生成 36MB 自包含 HTML**：
网页在这里按页现算、切图走现有 `/crop` 端点。这样表的「框数」永远和编辑器里
看到的一致（改完框回来刷新就是新的），不必再跑一遍 2.5 分钟的 build_strips。

三个来源：
* 字海1.0：`dict/*.ndjson` 里带 `zh_ref`（或 `zh_ref11`）的记录，
  `number` 是**页内位置号**（不是流水号，缺号处即 dict 漏收那一条）；
* 卷1：`_zihai/editor/edits.json`；
* 卷2：`edits_v2.json`，缺页时退回 `cache_v2`（审过未改）。
"""

from __future__ import annotations

import collections
import json
from pathlib import Path
from typing import Any

from . import config

_ZH_CACHE: dict[str, dict[int, list[tuple[int, str, str, str]]]] = {}


def _char_of(ziid: str) -> str:
    """U 系 ziid 就是码位（U04E00 → 一）；Z/P 系不含码位，只能靠雪碧图。"""
    if not ziid.startswith("U"):
        return ""
    try:
        code = int(ziid[1:], 16)
    except ValueError:
        return ""
    if (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x3FFFF
        or 0x2E80 <= code <= 0x2EFF
        or 0x3000 <= code <= 0x303F
    ):
        return chr(code)
    return ""


def zh_index() -> dict[int, list[tuple[int, str, str, str]]]:
    """{印刷页: [(位置号, 字形, ziid, 正字)]}。字典表很大（74MB），只解析一次。"""
    key = str(config.DATA_DIR)
    cached = _ZH_CACHE.get(key)
    if cached is not None:
        return cached
    grouped: dict[int, list[tuple[int, str, str, str]]] = collections.defaultdict(list)
    for path in sorted((config.DATA_DIR / "dict").glob("*.ndjson")):
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ref = record.get("zh_ref") or record.get("zh_ref11")
                if not isinstance(ref, dict):
                    continue
                page, number = ref.get("page"), ref.get("number")
                if not isinstance(page, int) or not isinstance(number, int):
                    continue
                ziid = str(record.get("ziid") or "")
                grouped[page].append((number, _char_of(ziid), ziid, str(record.get("standard_form") or "")))
    for rows in grouped.values():
        rows.sort()
    result = dict(grouped)
    _ZH_CACHE[key] = result
    return result


def notes() -> dict[str, list[dict[str, Any]]]:
    """核实过的、不算问题的人工标注（某一侧确实失收 / 重复占位 / 多切一框）。"""
    path = config.DATA_DIR / "_zihai" / "table" / "notes.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def columns_of(boxes: list[dict[str, Any]]) -> list[int]:
    """把一页的框按 x 中心的最大两个空隙切成栏（通常是左中右三栏）。"""
    if len(boxes) < 6:
        return [1] * len(boxes)
    centers = sorted((b["x"] + b["w"] / 2, i) for i, b in enumerate(boxes))
    gaps = sorted(((centers[k + 1][0] - centers[k][0], k) for k in range(len(centers) - 1)), reverse=True)[:2]
    cuts = sorted(k for _, k in gaps)
    col = [0] * len(boxes)
    for rank, (_, i) in enumerate(centers):
        level = 1
        for cut in cuts:
            if rank > cut:
                level += 1
        col[i] = level
    return col


def reading_order(boxes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按「栏 → 行 → 列」排出阅读顺序（表里的第 i 行就是这份顺序的第 i 个框）。"""
    if not boxes:
        return []
    col = columns_of(boxes)
    order = sorted(range(len(boxes)), key=lambda i: (col[i], boxes[i]["y"], boxes[i]["x"]))
    return [boxes[i] for i in order]


def _shifted(boxes: list[dict[str, Any]], page_notes: list[dict[str, Any]], side: str, rows: int):
    """按 notes 把某一侧的框对到位置号上：`miss` 留空，`extra` 跳过那个框。

    返回长度 = rows 的列表，元素是框或 None。
    """
    skipped = {int(n["pos"]) for n in page_notes if n.get("extra") == side and n.get("pos")}
    missing = {int(n["pos"]) for n in page_notes if n.get("miss") == side and n.get("pos")}
    usable = [b for i, b in enumerate(boxes, 1) if i not in skipped]
    out: list[dict[str, Any] | None] = []
    cursor = 0
    for pos in range(1, rows + 1):
        if pos in missing:
            out.append(None)
            continue
        out.append(usable[cursor] if cursor < len(usable) else None)
        cursor += 1
    return out


def build_rows(
    printed_page: int,
    v1: list[dict[str, Any]],
    v2: list[dict[str, Any]],
    page_notes: list[dict[str, Any]],
) -> dict[str, Any]:
    """把三方对齐成行。位置号取字海1.0；它漏收的地方按 notes 顺移。"""
    zh_rows = zh_index().get(printed_page, [])

    # 「并入」：字海1.0 把同一个字占了两个位置号，丢掉一个，后面的整体前移
    dropped = {int(n["pos"]) for n in page_notes if n.get("into") and n.get("pos")}
    zmap = {num: (char, ziid, zheng) for num, char, ziid, zheng in zh_rows if num not in dropped}
    zh_total = max(zmap) if zmap else 0

    v1_order = reading_order(v1)
    v2_order = reading_order(v2)

    # 判「位置对齐」：两卷有效框数一致（两次扫描互证），且字海位置号不超出框数。
    # 「有效」= 去掉多切的框、补上声明失收的位。
    a1 = len(v1_order) + sum(1 for n in page_notes if n.get("miss") == "v1")
    a1 -= sum(1 for n in page_notes if n.get("extra") == "v1")
    a2 = len(v2_order) + sum(1 for n in page_notes if n.get("miss") == "v2")
    a2 -= sum(1 for n in page_notes if n.get("extra") == "v2")

    rows = max(zh_total, a1, a2)
    v1_at = _shifted(v1_order, page_notes, "v1", rows)
    v2_at = _shifted(v2_order, page_notes, "v2", rows)

    note_by_pos: dict[int, str] = {}
    for item in page_notes:
        if item.get("note"):
            note_by_pos[int(item.get("pos") or 0)] = str(item["note"])

    out = []
    sprites = sprite_manifest()
    for pos in range(1, rows + 1):
        char, ziid, zheng = zmap.get(pos, ("", "", ""))
        # Z/P 系 ziid 不含码位，字形只能靠雪碧图
        has_sprite = natural_zid(printed_page, pos) in sprites
        out.append(
            {
                "pos": pos,
                "char": char,
                "ziid": ziid,
                "standard_form": zheng,
                "note": note_by_pos.get(pos, ""),
                "sprite": has_sprite,
                "v1": v1_at[pos - 1],
                "v2": v2_at[pos - 1],
            }
        )

    # 判「位置对齐」：两卷有效框数一致（两次扫描互证），且字海位置号不超出框数
    aligned = a1 == a2 and zh_total <= max(a1, a2)
    return {
        "rows": out,
        "counts": {"zh": len(zmap), "v1": len(v1_order), "v2": len(v2_order)},
        "effective": {"v1": a1, "v2": a2},
        "aligned": aligned,
        "notes": bool(page_notes),
    }


# ── 字海1.0 雪碧图（Z/P 系 ziid 不含码位，字形只能看图） ─────────────────
_B36 = "0123456789ABCDEFGH"


def natural_zid(page: int, pos: int) -> str:
    """(页, 页内位置) → 「假想 Z 号」，与 `_zihai/build_zihai_sprites.py` 一致。"""
    if not (0 <= page <= 1799 and 1 <= pos <= 99):
        return ""
    return "Z" + _B36[page // 100] + f"{(page % 100) * 100 + pos:04d}"


_SPRITE_CACHE: dict[str, dict[str, list[Any]]] = {}
_SHEET_CACHE: dict[str, Any] = {}
SHEET_CACHE_SIZE = 4


def _sprite_dir() -> Path:
    return config.DATA_DIR / "sprites_z"


def sprite_manifest() -> dict[str, list[Any]]:
    """{Z 号: [sheet, x, y]}。zh10 优先，缺的用 zh11 补（个别 1.0 失收的字只有 1.1 有图）。"""
    base = _sprite_dir()
    key = str(base)
    cached = _SPRITE_CACHE.get(key)
    if cached is not None:
        return cached
    table: dict[str, list[Any]] = {}
    for edition in ("zh11", "zh10"):  # 先 1.1 再 1.0，1.0 的覆盖 1.1
        path = base / edition / "manifest.ndjson"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                name = str(row.get("key") or "")
                if not name:
                    continue
                table[name] = [f"{edition}/sheets/{row.get('sheet')}", int(row["x"]), int(row["y"])]
    _SPRITE_CACHE[key] = table
    return table


def _sheet_image(rel: str) -> Any:
    import cv2
    import numpy as np

    cached = _SHEET_CACHE.get(rel)
    if cached is not None:
        return cached
    path = _sprite_dir() / rel
    arr = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if arr is None:
        raise FileNotFoundError(f"雪碧图读不出来：{path}")
    if len(_SHEET_CACHE) >= SHEET_CACHE_SIZE:
        _SHEET_CACHE.pop(next(iter(_SHEET_CACHE)))
    _SHEET_CACHE[rel] = arr
    return arr


def sprite_png(page: int, pos: int, max_size: int = 96) -> bytes | None:
    """按 (页, 位置) 取字海1.0 书影（128×128 格），取不到返回 None。"""
    entry = sprite_manifest().get(natural_zid(page, pos))
    if not entry:
        return None
    from .core import imaging

    sheet, x, y = entry
    arr = _sheet_image(str(sheet))
    patch = arr[int(y) : int(y) + 128, int(x) : int(x) + 128]
    if patch.size == 0:
        return None
    return imaging.encode_png(imaging.downscale(patch, max_size))
