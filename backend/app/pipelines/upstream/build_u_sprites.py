#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把字形轮廓文本打包成 U 系雪碧图（sheets/*.png + manifest.ndjson）。

输入是 `chart_pdf_to_paths.py` 产出的 `<字>_<变体>\t<svg path>`（单位 pt、基线 y=0、
y 轴向下，1em = 21pt）。

**每个来源各存一份**：同一字的 G/T/J/K/P/H/V/S/B/U/M/Q 全部落到雪碧图里
（sheet 名 `<前缀><来源>.png`，如 U04G.png），字条页可以把它们并排显示。
默认用哪个来源由后端按 G→T→J→K→P→H→V→S→B→U→M→Q 现算，不写死在图里。

**方块字网格**：每个字都按设计好的 em 方块对位，而不是按墨迹外框缩放 ——
em 方块 = 1em × 1em，基线在方块下方 12% 处（CJK 字体的普遍取法），于是
「一」不会撑满格子、「口」不会缩成小方块，所有字看起来就跟排版排出来的一样。
个别字的墨迹会略微出框（比如带长撇的重心偏字），这时按需**对称**外扩网格
（上限 2.5pt），保证不切笔画。

输出约定与现有雪碧图一致：

* sheet 宽 4096、格子 128×128、32 列、2048 格一张
* sheet 名 = `<来源字母><两位区块>`，即把 ziid 的 `U` 换成来源字母：
  `U20` + G → `G20.png`、`U0E` + G → `G0E.png`；超过 2048 格才拆成 `G20_0.png`、`G20_1.png`…
* manifest 一行一格 `{"ziid","src","sheet","x","y","w","h"}`，按 (ziid, 来源顺序) 排、LF 换行
* 格子里是白底黑字（前端 lib/sprites.js 负责白底→透明）

用法：

    python tools/build_u_sprites.py --paths temp/18.0.0.txt --out sprites_u
    python tools/build_u_sprites.py --paths ... --only U04 --workers 4
    python tools/build_u_sprites.py --paths ... --keep-from /backup/sprites_u   # 从别处取旧字

依赖：cairocffi（`pip install cairocffi`）、Pillow
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor

import cairocffi as cairo

CELL = 128
PAD_PX = 1.5
SHEET_WIDTH = 4096
COLS = SHEET_WIDTH // CELL
CELLS_PER_SHEET = 2048

EM = 21.0          # 1em 的坐标长度（与 txt 一致）
ASCENT = 0.88      # em 方块里基线以上的比例
MAX_PAD = 2.5      # 网格最多对称外扩多少 pt

# 来源的固定顺序（与后端 database.SPRITE_VARIANT_ORDER 一致）：决定 manifest 里
# 同一个字的多个来源谁在前，也就是「默认字形」谁排第一。
VARIANT_ORDER = "GTHJKPVSUBMQ"

# 默认不做的区段：小篆（U+3D000–3FC3F）。它不是正体汉字，暂时不出图；
# 轮廓文本里仍然保留，将来要做直接把 --exclude 传空即可。
DEFAULT_EXCLUDE = "3D000-3FC3F"

NUM_RE = re.compile(r"[-+]?\d*\.?\d+")


# ── path 工具 ──────────────────────────────────────────────────────────


def path_bbox(d: str):
    """路径的（含控制点的）外框 —— 只用来判断墨迹有没有出 em 方块。"""
    nums = [float(x) for x in NUM_RE.findall(d)]
    if len(nums) < 2:
        return None
    xs, ys = nums[0::2], nums[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def draw_path(ctx, d: str) -> None:
    toks = d.split()
    i = 0
    n = len(toks)
    while i < n:
        cmd = toks[i]
        i += 1
        if cmd == "M":
            ctx.move_to(float(toks[i]), float(toks[i + 1]))
            i += 2
        elif cmd == "L":
            ctx.line_to(float(toks[i]), float(toks[i + 1]))
            i += 2
        elif cmd == "C":
            ctx.curve_to(
                float(toks[i]), float(toks[i + 1]),
                float(toks[i + 2]), float(toks[i + 3]),
                float(toks[i + 4]), float(toks[i + 5]),
            )
            i += 6
        elif cmd == "Z":
            ctx.close_path()
        elif cmd in ("Q", "T"):
            raise ValueError(f"不支持的路径指令 {cmd}")


def cell_transform(bbox) -> tuple[float, float, float]:
    """按 em 方块算 (缩放, 平移 x, 平移 y)，单位 = 格子像素。"""
    em_left = 0.0
    em_top = -EM * ASCENT
    em_right = EM
    em_bottom = EM * (1.0 - ASCENT)
    if bbox is None:
        return (CELL - 2 * PAD_PX) / EM, PAD_PX, PAD_PX
    pad = 0.0
    if bbox[0] < em_left:
        pad = max(pad, em_left - bbox[0])
    if bbox[1] < em_top:
        pad = max(pad, em_top - bbox[1])
    if bbox[2] > em_right:
        pad = max(pad, bbox[2] - em_right)
    if bbox[3] > em_bottom:
        pad = max(pad, bbox[3] - em_bottom)
    pad = min(pad, MAX_PAD)

    half = EM / 2.0 + pad
    s = (CELL - 2 * PAD_PX) / (2.0 * half)
    cx = EM / 2.0
    cy = (em_top + em_bottom) / 2.0
    return s, CELL / 2.0 - cx * s, CELL / 2.0 - cy * s


def _write_sheet_png(surface, path: str, colors: int = 16) -> None:
    """cairo 的 ARGB32 直接存 PNG 要 4~8 MB/张；字形只有灰阶，量化到 16 级后体积降到
    1/4（和旧雪碧图相当），肉眼无差别。量化用固定色阶（含纯白），保证空白处仍是
    纯白 —— 前端靠四角纯白判断「白底图」。"""
    from PIL import Image

    buf = io.BytesIO()
    surface.write_to_png(buf)
    buf.seek(0)
    gray = Image.open(buf).convert("L")
    step = 255 // (colors - 1)
    post = gray.point(lambda v: min(colors - 1, (v + step // 2) // step) * step)
    levels = [i * step for i in range(colors)]
    pal = Image.new("P", (1, 1))
    pal.putpalette([c for v in levels for c in (v, v, v)] + [0] * (768 - 3 * colors))
    post.convert("RGB").quantize(palette=pal, dither=Image.NONE).save(path, optimize=True)


def render_sheet(job) -> tuple[str, int, str]:
    """worker：画一张 sheet。job = (名字, [(ziid, path 或 None)], 输出目录, 保留图 bytes)。"""
    name, cells, sheets_dir, keep_png = job
    rows = (len(cells) + COLS - 1) // COLS
    w, h = SHEET_WIDTH, rows * CELL
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    ctx = cairo.Context(surface)
    ctx.set_source_rgb(1, 1, 1)
    ctx.paint()
    ctx.set_source_rgb(0, 0, 0)
    for i, (ziid, d) in enumerate(cells):
        cx = (i % COLS) * CELL
        cy = (i // COLS) * CELL
        if d is None:
            data = keep_png.get(ziid)
            if data:
                surf = cairo.ImageSurface.create_from_png(io.BytesIO(data))
                ctx.set_source_surface(surf, cx, cy)
                ctx.paint()
                ctx.set_source_rgb(0, 0, 0)
            continue
        s, tx, ty = cell_transform(path_bbox(d))
        ctx.save()
        ctx.translate(cx + tx, cy + ty)
        ctx.scale(s, s)
        draw_path(ctx, d)
        ctx.fill()
        ctx.restore()
    path = os.path.join(sheets_dir, name)
    _write_sheet_png(surface, path)
    return name, len(cells), f"{w}x{h}"


# ── 读轮廓文本 ─────────────────────────────────────────────────────────


def iter_paths(paths_file: str):
    """按文件顺序产出 (字符, 变体, path)。文件本身已按码位升序。"""
    with open(paths_file, encoding="utf-8") as fh:
        for line in fh:
            name, sep, d = line.partition("\t")
            if not sep:
                continue
            ch, _, letter = name.rpartition("_")
            if ch and d.strip():
                yield ch, letter, d.strip()


# ── 旧雪碧图（补齐图表里没有的字） ────────────────────────────────────


class OldSprites:
    """读旧 manifest + sheets，把图表里没有的字（如未分配位的 U+2EF4–2EFF）原样搬过来。"""

    def __init__(self, base: str):
        self.base = base
        self.entries: dict[str, dict] = {}
        mp = os.path.join(base, "manifest.ndjson")
        if os.path.exists(mp):
            with open(mp, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    ziid = rec.get("ziid")
                    # 同一字有多来源时，优先取默认（第一条）
                    if ziid and ziid not in self.entries:
                        self.entries[ziid] = rec
        self._sheets: dict[str, object] = {}

    def _sheet(self, name: str):
        img = self._sheets.get(name)
        if img is None:
            from PIL import Image

            img = Image.open(os.path.join(self.base, "sheets", name)).convert("RGBA")
            self._sheets[name] = img
        return img

    def data_urls(self, ids) -> dict[str, bytes]:
        """把指定字的格子裁成 128×128 PNG 字节（开画之前先抓好，避免边写边读）。"""
        from PIL import Image

        out: dict[str, bytes] = {}
        for ziid in ids:
            rec = self.entries.get(ziid)
            if not rec:
                continue
            img = self._sheet(rec["sheet"]).crop(
                (rec["x"], rec["y"], rec["x"] + rec["w"], rec["y"] + rec["h"])
            )
            if img.size != (CELL, CELL):
                canvas = Image.new("RGBA", (CELL, CELL), (255, 255, 255, 255))
                canvas.paste(img, ((CELL - img.width) // 2, (CELL - img.height) // 2))
                img = canvas
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            out[ziid] = buf.getvalue()
        return out

    def close(self) -> None:
        for img in self._sheets.values():
            try:
                img.close()
            except Exception:  # noqa: BLE001
                pass
        self._sheets.clear()


# ── 主流程 ─────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", required=True, help="chart_pdf_to_paths.py 产出的 txt")
    ap.add_argument("--out", required=True, help="sprites_u 目录（已存在）")
    ap.add_argument("--only", default="", help="只做这些前缀（逗号分隔，如 U04,U20）")
    ap.add_argument("--exclude", default=DEFAULT_EXCLUDE,
                    help='不做的码位区段（"起始-结束" 十六进制，逗号分隔；传空串表示全做）')
    ap.add_argument("--drop", default="U0E",
                    help="保留旧字形时跳过这些 ziid 前缀（逗号分隔；U0E 是私用区，字典里没有字）")
    ap.add_argument("--keep-from", default="",
                    help="从哪个 sprites_u 目录取「图表里没有的旧字形」（默认就是 --out）")
    ap.add_argument("--workers", type=int, default=4, help="并行画 sheet 的进程数")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sheets_dir = os.path.join(args.out, "sheets")
    if not args.dry_run:
        os.makedirs(sheets_dir, exist_ok=True)

    only = {p.strip().upper() for p in args.only.split(",") if p.strip()}
    dropped = {p.strip().upper() for p in args.drop.split(",") if p.strip()}

    exclude: list[tuple[int, int]] = []
    for part in args.exclude.split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        lo = int(a, 16)
        hi = int(b, 16) if b else lo
        exclude.append((lo, hi))

    def excluded(cp: int) -> bool:
        return any(lo <= cp <= hi for lo, hi in exclude)

    rank = {c: i for i, c in enumerate(VARIANT_ORDER)}

    # 按 (前缀, 来源) 分组：同字多来源各占一格
    groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
    seen: set[str] = set()
    n_glyphs = 0
    for ch, letter, d in iter_paths(args.paths):
        cp = ord(ch)
        if excluded(cp) or letter not in rank:
            continue
        ziid = f"U{cp:05X}"
        if only and ziid[:3] not in only:
            continue
        groups.setdefault((ziid[:3], letter), []).append((ziid, d))
        seen.add(ziid)
        n_glyphs += 1
    print(f"轮廓 {n_glyphs} 个（{len(seen)} 字），分组 {len(groups)} 组", file=sys.stderr)

    # 图表里没有的旧字形：只补「整字都不存在」的，来源记 Q（代表字形）
    old = OldSprites(args.keep_from or args.out)
    keep_ids = [
        z for z in sorted(old.entries)
        if z not in seen
        and not excluded(int(z[1:], 16))
        and z[:3] not in dropped
        and (not only or z[:3] in only)
    ]
    keep_png = old.data_urls(keep_ids) if keep_ids else {}
    old.close()
    if keep_ids:
        print(f"旧字形补 {len(keep_ids)} 个（{keep_ids[0]}…{keep_ids[-1]}）", file=sys.stderr)
        for ziid in keep_ids:
            groups.setdefault((ziid[:3], "Q"), []).append((ziid, None))

    # 每组切张，排好 manifest
    jobs = []
    manifest: list[dict] = []
    for key in sorted(groups, key=lambda k: (k[0], rank.get(k[1], 99))):
        prefix, letter = key
        cells = sorted(groups[key], key=lambda c: c[0])
        chunks = [cells[i:i + CELLS_PER_SHEET] for i in range(0, len(cells), CELLS_PER_SHEET)]
        for ci, chunk in enumerate(chunks):
            base = f"{letter}{prefix[1:]}"
            name = f"{base}.png" if len(chunks) == 1 else f"{base}_{ci}.png"
            for i, (ziid, _d) in enumerate(chunk):
                manifest.append({
                    "ziid": ziid,
                    "src": letter,
                    "sheet": name,
                    "x": (i % COLS) * CELL,
                    "y": (i // COLS) * CELL,
                    "w": CELL,
                    "h": CELL,
                })
            jobs.append((name, chunk, sheets_dir, keep_png))

    print(f"{len(manifest)} 格 / {len(jobs)} 张 sheet", file=sys.stderr)
    if args.dry_run:
        for name, chunk, _, _ in jobs:
            rows = (len(chunk) + COLS - 1) // COLS
            print(f"  {name}: {len(chunk)} 格 4096x{rows * CELL}", file=sys.stderr)
        return

    done: set[str] = set()
    workers = max(1, min(args.workers, len(jobs)))
    if workers == 1:
        for job in jobs:
            name, n, size = render_sheet(job)
            done.add(name)
            print(f"  {name}: {n} 格 {size}", file=sys.stderr)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for name, n, size in pool.map(render_sheet, jobs, chunksize=1):
                done.add(name)
                print(f"  {name}: {n} 格 {size}", file=sys.stderr)

    # 清掉这次没写的旧 sheet（命名换了），再写 manifest
    for name in sorted(os.listdir(sheets_dir)):
        if name.lower().endswith(".png") and name not in done:
            os.remove(os.path.join(sheets_dir, name))
    mp = os.path.join(args.out, "manifest.ndjson")
    with open(mp, "w", encoding="utf-8", newline="\n") as fh:
        for rec in sorted(manifest, key=lambda r: (r["ziid"], rank.get(r["src"], 99))):
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"manifest → {mp}（{len(manifest)} 格）", file=sys.stderr)


if __name__ == "__main__":
    main()
