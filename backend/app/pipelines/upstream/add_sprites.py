#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把字形 PNG 放进雪碧图（sprites/sheets/*.png + sprites/manifest.ndjson）。

照现有雪碧图的约定
    * 一张 sheet = 4096 宽、每格 128×128 的网格（32 列）。
      Z/P 系：sheet 名 = ziid 前 3 位，如 P89966 → P89.png
      U 系：一个前缀可能拆成多张，名为 <前3位>_<序号>.png，如 U2B81E → U2B_1.png
    * manifest 一行一格 {"ziid", "sheet", "x", "y", "w", "h"}，按 ziid 升序；后端按 (x,y,w,h) 裁剪。
    * 格子里的字形：整图等比缩放到 128×128 之内、居中；manifest 记整格 (x, y, 128, 128)。
      现有格子绝大多数都是 128×128，字形占格子约九成。
    * manifest 是 CRLF、分片是 LF；本脚本只改要改的那几行，其余字节原样保留。
    * U/Z/P 各系都可加图：U 系有码位，通常靠字体渲染，但字体缺字时也可用雪碧图补字形。

用法
    python tools/add_sprites.py P89966.png U2B81E.png     # 只预览，不改文件
    python tools/add_sprites.py --write P89966.png ...   # 真正写入

    文件名（去扩展名）就是 ziid。U 系按 _序号 依次找第一个有空格子的 sheet；
    都没有空位就在最后一张加一行。Z/P 系用单张 <前3位>.png，没空位就加一行。
"""

import argparse
import bisect
import collections
import json
import os
import re
import sys

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.exit("需要 Pillow：用站点仓库的 .venv 跑，或 pip install pillow")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CELL = 128
SHEET_WIDTH = 4096
COLS = SHEET_WIDTH // CELL
# 三块雪碧图：U 系、P 系、字海书影（Z 系的字形已全部来自书影，不再手工加）
SPRITE_GROUPS = {
    "U": ("sprites_u",),
    "P": ("sprites_p",),
}
# U 系找空位时的 sheet 顺序（= 来源字形优先级，见 build_u_sprites.py）
SPRITE_ORDER = "GTHJKPVSUBMQ"
MANIFEST = None
SHEETS_DIR = None


def set_group(ziid: str) -> None:
    """按 ziid 首字母切换当前操作的雪碧图目录。"""
    global MANIFEST, SHEETS_DIR
    parts = SPRITE_GROUPS.get(ziid[0])
    if not parts:
        raise SystemExit(f"{ziid}: 只支持 U 系和 P 系；Z 系字形来自《中华字海》切图，"
                         f"请用 _zihai/pipeline/build_zihai_sprites.py 重新生成")
    base = os.path.join(REPO_ROOT, *parts)
    MANIFEST = os.path.join(base, "manifest.ndjson")
    SHEETS_DIR = os.path.join(base, "sheets")


def read_manifest():
    """返回 (逐行文本, [(ziid, 行号)])。逐行文本保留原换行符。"""
    with open(MANIFEST, encoding="utf-8", newline="") as fh:
        lines = fh.read().splitlines(keepends=True)
    ids = [(json.loads(line)["ziid"], i) for i, line in enumerate(lines) if line.strip()]
    return lines, ids


def sheet_candidates(ziid: str) -> list[str]:
    """该 ziid 可能落在的 sheet 名（按优先级）。

    U 系的 sheet 名是「来源字母 + 两位区块」，如 U20 的 G 源 → G20.png，
    一个区块装不下才拆成 G20_0.png / G20_1.png；这里在现有来源字母里按
    SPRITE_ORDER 找第一个有空位的，都没有就用 Q（代表字形）开一张。
    Z/P 系仍是单张 <前3位>.png。
    """
    prefix = ziid[:3]
    if ziid[0] != "U":
        return [prefix + ".png"]

    block = prefix[1:]                      # U20 -> 20
    pattern = re.compile(r"^([A-Z])" + re.escape(block) + r"(?:_(\d+))?\.png$")
    found: dict[str, list[tuple[int, str]]] = {}
    for name in os.listdir(SHEETS_DIR):
        m = pattern.match(name)
        if m:
            found.setdefault(m.group(1), []).append((int(m.group(2) or 0), name))

    out: list[str] = []
    for letter in SPRITE_ORDER:
        if letter in found:
            out.extend(name for _i, name in sorted(found[letter]))
    return out or [f"Q{block}.png"]


def _overlaps(a: tuple, b: tuple) -> bool:
    """两个矩形 (x,y,w,h) 是否相交。"""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


def free_cells(entries, sheet: str, count: int, max_rows: int = 64):
    """sheet 上还没被占用的前 count 个格子（行优先），最多看 max_rows 行。

    manifest 里的矩形不一定正好是整格（多数是紧贴墨迹的 124×124 之类），
    所以按 **矩形相交** 判断，而不是坐标是否相同。
    sheet 不存在时按空表算。返回的格子数可能少于 count（说明这张放不下）。
    """
    boxes = [(e["x"], e["y"], e["w"], e["h"]) for e in entries if e["sheet"] == sheet]
    cells = []
    for row in range(max_rows):
        for col in range(COLS):
            cell = (col * CELL, row * CELL, CELL, CELL)
            if any(_overlaps(cell, b) for b in boxes):
                continue
            cells.append((col * CELL, row * CELL))
            if len(cells) == count:
                return cells
    return cells


def fit(img, size: int = CELL):
    w, h = img.size
    k = min(size / w, size / h)
    return img.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)


def paste_into_sheet(sheet: str, img, x: int, y: int):
    """把图居中贴进 sheet 的 (x, y) 格子；sheet 不存在就新建，不够高就先加一行。返回是否加行。"""
    path = os.path.join(SHEETS_DIR, sheet)
    if os.path.exists(path):
        page = Image.open(path)
        if page.mode not in ("RGBA", "RGB"):
            page = page.convert("RGB")
    else:
        page = Image.new("RGBA", (SHEET_WIDTH, 0), (255, 255, 255, 0))
    if img.mode not in ("RGBA", "RGB"):
        img = img.convert("RGB")

    old_h = page.size[1]
    grew = y + CELL > old_h
    if grew:  # 往下加行（白底/透明底）
        bg = (255, 255, 255, 255) if page.mode == "RGBA" else (255, 255, 255)
        base = Image.new(page.mode, (SHEET_WIDTH, y + CELL), bg)
        base.paste(page, (0, 0))
        page.close()
        page = base

    box = (x + (CELL - img.width) // 2, y + (CELL - img.height) // 2)
    if img.mode == "RGBA" and page.mode == "RGBA":
        page.alpha_composite(img, box)
    else:
        page.paste(img, box)
    page.save(path)
    page.close()
    return grew


def update_manifest(lines, ids, planned, eol):
    """已有 ziid 改指向，新 ziid 按 ziid 升序插入；其余行原样保留。"""
    order = [ziid for ziid, _ in ids]  # 与 line 行号对应，已按 ziid 升序
    pos = {ziid: i for ziid, i in ids}
    for p in planned:
        entry = {"ziid": p["ziid"], "sheet": p["sheet"], "x": p["x"], "y": p["y"], "w": CELL, "h": CELL}
        text = json.dumps(entry, ensure_ascii=False) + eol
        if p["ziid"] in pos:
            lines[pos[p["ziid"]]] = text
            continue
        k = bisect.bisect_left(order, p["ziid"])  # 插到第 k 条之前
        at = pos[order[k - 1]] + 1 if k else 0
        lines.insert(at, text)
        pos = {z: (i + 1 if i >= at else i) for z, i in pos.items()}
        pos[p["ziid"]] = at
        order.insert(k, p["ziid"])
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="把字形 PNG 加进雪碧图")
    ap.add_argument("images", nargs="+", help="图片路径；文件名（去扩展名）当 ziid")
    ap.add_argument("--write", action="store_true", help="真正写文件（默认只预览）")
    args = ap.parse_args()

    planned_all = []
    for path in args.images:
        ziid = os.path.splitext(os.path.basename(path))[0].upper()
        if not os.path.exists(path):
            raise SystemExit(f"找不到图片：{path}")
        planned_all.append({"ziid": ziid, "path": path})

    # 按 U / P 分组，每组一次读写自己的 manifest
    groups = collections.OrderedDict()
    for p in planned_all:
        groups.setdefault(p["ziid"][0], []).append(p)

    rc = 0
    for first, planned in groups.items():
        set_group(planned[0]["ziid"])
        rc |= _add_one_group(planned, args.write)
    return rc


def _add_one_group(planned, write: bool) -> int:
    lines, ids = read_manifest()
    eol = "\r\n" if lines[0].endswith("\r\n") else "\n"
    entries = [json.loads(ln) for ln in lines if ln.strip()]
    existing = {e["ziid"] for e in entries}

    # 分配格子：每张 sheet 最多 64 行；U 系按 _序号 往下找，放不下就开新的一张
    for p in planned:
        ziid = p["ziid"]
        placed = False
        for sheet in sheet_candidates(ziid):
            room = free_cells(entries, sheet, 1)
            if room:
                x, y = room[0]
                p["sheet"], p["x"], p["y"] = sheet, x, y
                entries.append({"ziid": ziid, "sheet": sheet, "x": x, "y": y, "w": CELL, "h": CELL})
                placed = True
                break
        if not placed:
            # 所有已有 sheet 都满：U 系开下一个序号，Z/P 在最后一张加行
            cands = sheet_candidates(ziid)
            last = cands[-1]
            if ziid[0] == "U":
                n = max((int(c[len(ziid[:3]) + 1 : -4]) for c in cands), default=-1) + 1
                sheet = f"{ziid[:3]}_{n}.png"
                x, y = 0, 0
            else:
                rows = Image.open(os.path.join(SHEETS_DIR, last)).height // CELL
                sheet, x, y = last, 0, rows * CELL
            p["sheet"], p["x"], p["y"] = sheet, x, y
            entries.append({"ziid": ziid, "sheet": sheet, "x": x, "y": y, "w": CELL, "h": CELL})

    for p in planned:
        with Image.open(p["path"]) as src:
            size = src.size
        print(
            f"{p['ziid']}  {os.path.basename(p['path'])} {size} → {p['sheet']} 格子({p['x']},{p['y']}) "
            f"{'改指向' if p['ziid'] in existing else '新增条目'}"
        )
    if not write:
        print("预览结束，没动文件。加 --write 才写入。")
        return 0

    for p in planned:
        with Image.open(p["path"]) as src:
            grew = paste_into_sheet(p["sheet"], fit(src), p["x"], p["y"])
        print(f"  已贴图 {p['ziid']} → {p['sheet']} ({p['x']},{p['y']}){'（加了一行）' if grew else ''}")
    lines = update_manifest(lines, ids, planned, eol)
    with open(MANIFEST, "w", encoding="utf-8", newline="") as fh:
        fh.write("".join(lines))
    print(f"manifest 已更新（共 {len(lines)} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
