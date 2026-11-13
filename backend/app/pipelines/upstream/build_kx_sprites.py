"""康熙字典（内府本）净字 → 雪碧图 `sprites_kx/`（**按版面位置切**）。

位置来自 GlyphWiki 字表 `Group:simch-kx_KangXiDict` 的 `t` 系字形名，形如
`simch-kx_t007501一00` = 版面页 0075 第 01 位、「一」、变体 00。
**一条位置一个格子**：字典里同一个字常见两见（分部不同/笔画数不同，或正文与古文并列），
两处各出各的图，不共用、不借用。

键是隐编号（前端不展示），定长 6 位 `K` + 页百位 `0-Z` + 页后两位 `00-99`
+ 位次十位 `0-Z` + 位次个位 `0-9`：0075 页第 1 位 → `K07501`，1585 页第 7 位 → `KF8507`。

字形来源：
* 内府本上游已给出该位置的净字 PNG（`kangxi-1716` 的 `sources/glyphs-original.zip`，
  逐条记录一个文件）→ 直接缩放成格；
* 上游没有该位置（多为正文里并列的「古文」字形，约 2,300 处）→ 从 300dpi 扫描
  （`_up/kangxi/book/*.pdf`）按版面位置补切：位置就在前一条字头的正下方，
  用行距定位，再取窗口里最靠上的一段墨迹。

用法（数据仓库根目录）：
    python tools/kx_pagemap.py              # 一次性：建「上游页 → 扫描页序」映射
    python tools/build_kx_sprites.py        # 只统计、不写图
    python tools/build_kx_sprites.py --write
"""
import argparse
import collections
import difflib
import glob
import io
import json
import os
import re
import sys

import numpy as np
import pymupdf
from PIL import Image, ImageOps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UP = os.path.join(ROOT, "_up", "kangxi")
BOOK = os.path.join(UP, "book")
CACHE = os.path.join(UP, "cache")
DEFAULT_GROUP = os.path.join(UP, "group-simch-kx-KangXiDict.txt")
DEFAULT_GLYPHS = os.path.join(UP, "glyphs-src")
DEFAULT_MANIFEST = os.path.join(UP, "kangxi-1716", "sources", "manifest.json")

CELL = 128
COLS = 32
MARGIN = 2
B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
STEP = 366.0        # 列内相邻两处字头的行距（实测中位数）
MIN_OVERLAP = 0.60  # 「扫描按矩形现裁」与上游那张的重合度下限，低于此值改用上游图
MIN_DET_SHAPE = 0.28  # 自动定位结果的可信下限：抽检 <0.30 的一半抓到了邻字，宁可退回同字图

F_CHAR = "\u5b57"
F_CAT = "\u7c7b\u522b"
F_FILE = "\u6587\u4ef6"
F_VOL = "\u518c"
F_PAGE = "PDF\u9875"
F_FACE = "\u9762"
F_ROW = "\u884c(\u81ea\u53f3)"
F_SITE = "\u7f51\u7ad9\u9875\u7801"
F_IDX = "\u9875\u5185\u5e8f\u53f7"
H_HEAD = "\u5b57\u5934"
H_DUP = "\u91cd\u590d\u5b57\u5934"


# ── 位置号 ─────────────────────────────────────────────────────────────

def kx_key(page, index):
    """版面页/位次 → 隐编号 Kxddxd。"""
    return "K%s%02d%s%d" % (B36[page // 100], page % 100, B36[index // 10], index % 10)


def e_key(e):
    return kx_key(e["page"], e["index"])


def parse_key(key):
    if len(key) != 6 or key[0] != "K":
        return None, None
    try:
        return B36.index(key[1]) * 100 + int(key[2:4]), B36.index(key[4]) * 10 + int(key[5])
    except ValueError:
        return None, None


# ── 字表 ───────────────────────────────────────────────────────────────

def parse_group(path):
    entries = []
    seen = set()
    section = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            m = re.match(r"^グループ:simch-kx_(\S+)$", s)
            if m:
                section = m.group(1)
                continue
            for edition, page, index, tail in re.findall(
                r"simch-kx_([td])(\d{4})(\d{2})([^\t\s]+)", s
            ):
                if edition != "t":
                    continue
                ident = (page, index, tail)
                if ident in seen:
                    continue
                seen.add(ident)
                entries.append({
                    "page": int(page), "index": int(index),
                    "char": tail[:-2], "variant": tail[-2:], "section": section,
                })
    entries.sort(key=lambda e: (e["page"], e["index"]))
    return entries


def ziid_of(char):
    if len(char) != 1:
        return None
    cp = ord(char)
    if not (0x3400 <= cp <= 0x4DBF or 0x4E00 <= cp <= 0x9FFF
            or 0x20000 <= cp <= 0x3FFFF or 0xF900 <= cp <= 0xFAFF):
        return None
    return "U%05X" % cp


# ── 内府本记录 ─────────────────────────────────────────────────────────

def load_records(path):
    raw = json.load(open(path, encoding="utf-8"))
    out = []
    for r in raw:
        if r[F_CAT] not in (H_HEAD, H_DUP):
            continue
        idx = r[F_IDX]
        out.append({
            "id": r["id"], "char": r[F_CHAR], "cat": r[F_CAT], "file": r[F_FILE],
            "vol": str(r[F_VOL]).zfill(2), "pdf": int(r[F_PAGE]), "face": r[F_FACE],
            "row": int(r[F_ROW]) if str(r[F_ROW]).isdigit() else 0,
            "site": r[F_SITE], "index": int(idx) if str(idx).isdigit() else 0,
            "x0": int(r["\u9875\u9762x0"]), "y0": int(r["\u9875\u9762y0"]),
            "x1": int(r["\u9875\u9762x1"]), "y1": int(r["\u9875\u9762y1"]),
            "pitch": float(r.get("pitch_px") or 190.0),
        })
    out.sort(key=lambda r: (r["site"], r["index"], r["row"]))
    return out


def match_entries(entries, records):
    """字表位置 → 内府本记录。

    1. 整序列按字对齐（difflib），吃掉两边各自的缺漏；
    2. 还没配上的位置，按「同字」找还没用掉的记录（重复字头等）；
    3. 仍没配上的，按「同一版面页上的记录」再对一次：同页内按字对齐，
       把上一步漏掉的记录捡回来。
    """
    heads = [r for r in records if r["cat"] == H_HEAD]
    dups = [r for r in records if r["cat"] == H_DUP]
    pairs = {}
    sm = difflib.SequenceMatcher(a=[e["char"] for e in entries],
                                 b=[r["char"] for r in heads], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                pairs[id(entries[i1 + k])] = heads[j1 + k]
    used = {r["id"] for r in pairs.values()}
    by_char = collections.defaultdict(list)
    for r in heads + dups:
        by_char[r["char"]].append(r)
    for e in entries:
        if id(e) in pairs:
            continue
        for cand in by_char.get(e["char"], []):
            if cand["id"] not in used:
                pairs[id(e)] = cand
                used.add(cand["id"])
                break

    # 同页再对一次：先把「位置 → 页」和「记录 → 页」各自分组
    left_entries = [e for e in entries if id(e) not in pairs]
    left_records = [r for r in heads + dups if r["id"] not in used]
    if left_entries and left_records:
        # 用已配上的位置反推 版面页 → 记录所在 (册, PDF页)
        page_key = {}
        for e in entries:
            rec = pairs.get(id(e))
            if rec is None:
                continue
            k = (rec["vol"], rec["pdf"])
            page_key.setdefault(e["page"], collections.Counter())[k] += 1
        page_to_pdf = {p: c.most_common(1)[0][0] for p, c in page_key.items()}
        pdf_to_page = {}
        for p, k in page_to_pdf.items():
            pdf_to_page.setdefault(k, p)
        by_pdf = collections.defaultdict(list)
        for r in left_records:
            by_pdf[(r["vol"], r["pdf"])].append(r)
        by_page = collections.defaultdict(list)
        for e in left_entries:
            by_page[e["page"]].append(e)
        for page, ents in by_page.items():
            k = page_to_pdf.get(page)
            if k is None or k not in by_pdf:
                continue
            ents.sort(key=lambda e: e["index"])
            cands = sorted(by_pdf[k], key=lambda r: (r["site"], r["index"], r["row"]))
            sm2 = difflib.SequenceMatcher(a=[e["char"] for e in ents],
                                          b=[r["char"] for r in cands], autojunk=False)
            for op, i1, i2, j1, j2 in sm2.get_opcodes():
                if op == "equal":
                    for t in range(i2 - i1):
                        pairs[id(ents[i1 + t])] = cands[j1 + t]

    out = {}
    for e in entries:
        rec = pairs.get(id(e))
        if rec is not None:
            out[(e["page"], e["index"])] = rec
    used_ids = {r["id"] for r in out.values()}
    return out, sm.ratio(), [r for r in records if r["id"] not in used_ids]


# ── 扫描页 ─────────────────────────────────────────────────────────────

class Volume:
    """一册扫描 PDF：按页解码，只保留当前页。"""

    def __init__(self, vol):
        paths = [p for p in glob.glob(os.path.join(BOOK, "*.pdf"))
                 if os.path.basename(p).split(".")[1] == vol]
        if not paths:
            raise FileNotFoundError("找不到册 %s 的 PDF" % vol)
        self.doc = pymupdf.open(paths[0])
        self.cur = (None, None)

    def array(self, idx):
        if self.cur[0] == idx:
            return self.cur[1]
        self.cur = (idx, None)
        if idx < 0 or idx >= self.doc.page_count:
            return None
        imgs = self.doc[idx].get_images(full=True)
        if not imgs:
            return None
        best = max(imgs, key=lambda i: i[2] * i[3])
        raw = self.doc.extract_image(best[0])
        a = np.array(Image.open(io.BytesIO(raw["image"])).convert("L"), dtype=np.uint8)
        self.cur = (idx, a)
        return a

    def close(self):
        self.doc.close()


_PM_CACHE = {}


def load_pagemap(vol):
    if vol in _PM_CACHE:
        return _PM_CACHE[vol]
    path = os.path.join(CACHE, "pagemap_%s.json" % vol)
    pm = None
    if os.path.exists(path):
        pm = {int(k): v for k, v in json.load(open(path, encoding="utf-8"))["map"].items()}
    _PM_CACHE[vol] = pm
    return pm


# ── 出格 ───────────────────────────────────────────────────────────────

def fit_cell(im):
    """任意灰图 → 128×128 黑白格（等比缩放居中，四周留 2px）。"""
    w, h = im.size
    box = CELL - MARGIN * 2
    scale = min(box / w, box / h)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    if (nw, nh) != (w, h):
        im = im.resize((nw, nh), Image.LANCZOS)
    cell = Image.new("L", (CELL, CELL), 255)
    cell.paste(im, ((CELL - nw) // 2, (CELL - nh) // 2))
    return cell.point(lambda v: 255 if v >= 128 else 0).convert("1")


def cell_from_glyph(path):
    """上游净字 PNG → 128×128 黑白格。"""
    im = Image.open(path).convert("L")
    bbox = ImageOps.invert(im).getbbox()
    if bbox:
        im = im.crop(bbox)
    return fit_cell(im)


def ink_mask(a):
    paper = float(np.percentile(a, 80))
    thr = max(58.0, paper * 0.76)
    return a < thr


def glyph_template(path):
    """上游净字 PNG → 紧贴字形的 0/1 模板（用于在扫描里找同一字形的另一处）。"""
    im = Image.open(path).convert("L")
    a = np.array(im) < 128
    ys, xs = np.where(a)
    if len(ys) == 0:
        return None
    return a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def font_template(ch, px=155):
    """参考字体渲染的字形模板（只在没有该字已切图时用于补切）。"""
    from PIL import ImageDraw, ImageFont
    path = os.path.join(UP, "kangxi-1716", "fonts", "OFLKangxi-Regular.otf")
    if not os.path.exists(path) or len(ch) != 1:
        return None
    font = ImageFont.truetype(path, px)
    im = Image.new("L", (px * 3, px * 3), 255)
    ImageDraw.Draw(im).text((px // 2, px // 3), ch, fill=0, font=font)
    a = np.array(im) < 128
    ys, xs = np.where(a)
    if len(ys) == 0 or ys.max() - ys.min() < px * 0.3:
        return None
    return a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def locate_in_scan(arr, tpl, region):
    """在 region 里按模板找同一个字形（多尺度），返回 (score, box) 或 (0, None)。"""
    import cv2
    x0, y0, x1, y1 = region
    h, w = arr.shape
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(w, int(x1)), min(h, int(y1))
    if x1 - x0 < 40 or y1 - y0 < 40:
        return 0.0, None
    sub = (ink_mask(arr[y0:y1, x0:x1]) * 255).astype(np.uint8)
    best = (0.0, None)
    for scale in (0.80, 0.90, 1.00, 1.10, 1.22, 1.35):
        tw, th = int(round(tpl.shape[1] * scale)), int(round(tpl.shape[0] * scale))
        if tw < 20 or th < 20 or tw >= sub.shape[1] or th >= sub.shape[0]:
            continue
        t = cv2.resize((tpl * 255).astype(np.uint8), (tw, th), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(sub, t, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx > best[0]:
            best = (float(mx), (x0 + loc[0], y0 + loc[1], x0 + loc[0] + tw, y0 + loc[1] + th))
    return best


def _column_band(arr, x_center, half, y0, y1, max_runs=8):
    """在列带里从上往下找第一段「大字」，并且只保留靠近列中心的笔画。

    版式里每一列的大字之后是双行小字注；小字的笔画细、常常紧贴左侧下一列，
    所以按连通块过滤：只取中心落在列中心附近的块，再取它们的并集外框。
    """
    import cv2
    h, w = arr.shape
    x0, x1 = max(0, int(x_center - half)), min(w, int(x_center + half))
    y0, y1 = max(0, int(y0)), min(h, int(y1))
    if x1 - x0 < 30 or y1 - y0 < 40:
        return None
    sub = ink_mask(arr[y0:y1, x0:x1])
    rows = sub.sum(1) > 0
    runs = []
    start = None
    gap = 0
    last = -1
    for i, v in enumerate(rows):
        if v:
            if start is None:
                start = i
            gap = 0
            last = i
        elif start is not None:
            gap += 1
            if gap > 6:
                runs.append((start, last))
                start = None
                if len(runs) >= max_runs:
                    break
    if start is not None:
        runs.append((start, last))

    cx = x_center - x0
    keep_half = half * 0.80
    for a, b in runs:
        piece = sub[a:b + 1]
        n, lab, stats, cent = cv2.connectedComponentsWithStats(piece.astype(np.uint8), 8)
        xs0, ys0, xs1, ys1 = None, None, None, None
        for i in range(1, n):
            bx, by, bw, bh, area = stats[i]
            if area < 40:
                continue
            if abs(cent[i][0] - cx) > keep_half:
                continue
            xs0 = bx if xs0 is None else min(xs0, bx)
            ys0 = by if ys0 is None else min(ys0, by)
            xs1 = bx + bw if xs1 is None else max(xs1, bx + bw)
            ys1 = by + bh if ys1 is None else max(ys1, by + bh)
        if xs0 is None:
            continue
        band = piece[ys0:ys1, xs0:xs1]
        if _band_ok(band):
            return band
    return None


def cut_from_scan(arr, rec, face_top, tpl, step=STEP, min_score=0.55):
    """在 `rec` 那一处的下方找这一处位置的字形，返回 (cell, 匹配分)。

    用模板在「同列下方 / 左边几列的顶部」这一带里找；找不到返回 (None, 最高分)。
    不退回「凭版面猜」的做法 —— 猜出来的多半抓到邻列小字，宁可空着。
    """
    h, w = arr.shape
    cx = (rec["x0"] + rec["x1"]) / 2.0
    pitch = rec["pitch"]
    gw = max(120.0, (rec["x1"] - rec["x0"]) + 20.0)
    half = min(pitch * 0.45, gw / 2.0 + 10)

    if tpl is None:
        return None, 0.0
    # 搜索范围：同列下方 ~3 行、左边 ~3 列（补切位置多数就在这几行/列之内）
    region = (cx - pitch * 3.2 - half, rec["y1"] - step * 0.10,
              cx + half, rec["y1"] + step * 3.2)
    score, box = locate_in_scan(arr, tpl, region)
    if box is not None and score >= min_score:
        bw, bh = box[2] - box[0], box[3] - box[1]
        ar_t = tpl.shape[1] / max(1.0, float(tpl.shape[0]))
        ar_b = bw / max(1.0, float(bh))
        if 100 <= bh <= 240 and 45 <= bw <= 260 and abs(ar_b - ar_t) <= max(0.35, ar_t * 0.45):
            crop = arr[max(0, box[1] - 4):box[3] + 4, max(0, box[0] - 4):box[2] + 4]
            return fit_cell(Image.fromarray(crop)), score
    return None, score

def _band_ok(band):
    """这一段墨迹像不像「大字」：够高、笔画够粗、不是实心块/细长条。"""
    h, w = band.shape
    if h < 45 or w < 25:
        return False
    if h > w * 4.5 or w > h * 4.0:
        return False
    density = band.mean()
    if density > 0.52 or density < 0.02:
        return False
    import cv2
    dt = cv2.distanceTransform(band.astype(np.uint8), cv2.DIST_L2, 5)
    return float(dt.max()) >= 5.0


def _find_band(arr, x0, x1, y0, y1, max_runs=8):
    """在窗口里从上往下找第一段合格（大字）的墨迹。"""
    h, w = arr.shape
    x0, x1 = max(0, int(x0)), min(w, int(x1))
    y0, y1 = max(0, int(y0)), min(h, int(y1))
    if x1 - x0 < 30 or y1 - y0 < 40:
        return None
    sub = ink_mask(arr[y0:y1, x0:x1])
    rows = sub.sum(1) > 0
    runs = []
    start = None
    gap = 0
    last = -1
    for i, v in enumerate(rows):
        if v:
            if start is None:
                start = i
            gap = 0
            last = i
        elif start is not None:
            gap += 1
            if gap > 6:
                runs.append((start, last))
                start = None
                if len(runs) >= max_runs:
                    break
    if start is not None:
        runs.append((start, last))
    for a, b in runs:
        band = sub[a:b + 1]
        cols = np.where(band.any(0))[0]
        if len(cols) == 0:
            continue
        band = band[:, cols[0]:cols[-1] + 1]
        if _band_ok(band):
            return band
    return None


def norm_mask(a, size=64):
    ys, xs = np.where(a)
    if len(ys) == 0:
        return None
    a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    im = Image.fromarray((a * 255).astype(np.uint8)).resize((size, size), Image.LANCZOS)
    return np.array(im) > 127


def overlap(cell_a, cell_b, size=64):
    """两格图的墨迹重合度（IoU，按各自墨迹外框归一化到 64×64 再算）。"""
    a = norm_mask(np.array(cell_a.convert("L")) < 128, size)
    b = norm_mask(np.array(cell_b.convert("L")) < 128, size)
    if a is None or b is None:
        return 0.0
    u = (a | b).sum()
    return 0.0 if not u else float((a & b).sum()) / float(u)


def cut_rect(arr, rec, pad=4):
    """?????????????????????????

    ????????????? 4px???????? ? 0.62??????? 6 ?????
    ???????????????????????? ?? ????????????
    ???????????????????????????
    """
    import cv2
    h, w = arr.shape
    x0, y0 = max(0, rec["x0"] - pad), max(0, rec["y0"] - pad)
    x1, y1 = min(w, rec["x1"] + pad), min(h, rec["y1"] + pad)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    sub = arr[y0:y1, x0:x1]
    paper = float(np.percentile(sub, 85))
    ink = (sub < paper * 0.62).astype(np.uint8)
    if ink.sum() < 30:
        return None
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    keep = np.zeros_like(ink)
    for i in range(1, n):
        if stats[i][4] >= 6:
            keep[lab == i] = 1
    if keep.sum() < 30:
        return None
    ys, xs = np.where(keep)
    band = keep[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    if band.shape[0] < 20 or band.shape[1] < 10:
        return None
    img = np.where(band > 0, 0, 255).astype(np.uint8)
    return fit_cell(Image.fromarray(img))


def save_sheets(cells, out_dir, rows):
    sheets_dir = os.path.join(out_dir, "sheets")
    os.makedirs(sheets_dir, exist_ok=True)
    for old in os.listdir(sheets_dir):
        if old.endswith(".png"):
            os.remove(os.path.join(sheets_dir, old))

    manifest = []
    by_bucket = collections.OrderedDict()
    for c in sorted(cells, key=lambda c: (c["page"], c["index"])):
        by_bucket.setdefault(c["page"] // 100, []).append(c)

    done = 0
    for bucket, group in by_bucket.items():
        chunk = COLS * rows
        for part, start in enumerate(range(0, len(group), chunk)):
            name = "K%02d%s.png" % (bucket, "" if part == 0 else "_%d" % part)
            canvas = Image.new("1", (COLS * CELL, rows * CELL), 1)
            n = min(len(group) - start, chunk)
            for k in range(n):
                c = group[start + k]
                x, y = (k % COLS) * CELL, (k // COLS) * CELL
                canvas.paste(c["cell"], (x, y))
                manifest.append({
                    "key": c["key"], "ziid": c["ziid"], "char": c["char"],
                    "variant": c["variant"], "sheet": name,
                    "x": x, "y": y, "w": CELL, "h": CELL,
                })
            used = (n + COLS - 1) // COLS
            if used < rows:
                canvas = canvas.crop((0, 0, COLS * CELL, used * CELL))
            canvas.save(os.path.join(sheets_dir, name), optimize=True)
            done += n
            print("  %s  %d 格（累计 %d）" % (name, n, done), flush=True)
    return manifest


# ── 主流程 ─────────────────────────────────────────────────────────────

def build(glyph_dir, out_dir, rows, write, report_path, cut_all=False):
    entries = parse_group(DEFAULT_GROUP)
    records = load_records(DEFAULT_MANIFEST)
    matched, ratio, unused = match_entries(entries, records)
    print("字表位置 %d；内府本记录 %d；对齐 %.4f；直取 %d；需补切 %d；闲置记录 %d"
          % (len(entries), len(records), ratio, len(matched),
             len(entries) - len(matched), len(unused)), flush=True)
    # 上游没有矩形的位置，靠 tools/kx_locate.py 自动定位出的大字框（形状分已把关）
    detected = {}
    det_path = os.path.join(CACHE, "kx-detected.json")
    if os.path.exists(det_path):
        raw = json.load(open(det_path, encoding="utf-8"))
        detected = {k: v for k, v in raw.items() if v.get("shape", 0) >= MIN_DET_SHAPE}
        print("自动定位坐标 %d 处（%s；形状分 <%.2f 的 %d 处不用）"
              % (len(detected), os.path.basename(det_path), MIN_DET_SHAPE,
                 len(raw) - len(detected)), flush=True)

    page_scan = {}
    for (page, index), rec in sorted(matched.items()):
        if page in page_scan:
            continue
        pm = load_pagemap(rec["vol"])
        if pm and rec["pdf"] in pm:
            page_scan[page] = (rec["vol"], pm[rec["pdf"]])

    # 每个扫描页上「字头区顶部」的估计：该页所有记录里最小的 y0
    top_of = {}
    for rec in records:
        pm = load_pagemap(rec["vol"])
        if not pm or rec["pdf"] not in pm:
            continue
        k = (rec["vol"], pm[rec["pdf"]])
        if k not in top_of or rec["y0"] < top_of[k]:
            top_of[k] = rec["y0"]

    per_page = collections.defaultdict(list)
    for e in entries:
        per_page[e["page"]].append(e)
    for page in per_page:
        per_page[page].sort(key=lambda e: e["index"])

    # 每个位置：自己的记录、同页前一条有记录的位置、所在扫描页
    # 扫描页优先用「该记录自己的册/页」推得（同一版面页上的记录可能来自不同书页），
    # 没有记录的才退回版面页的映射。
    pm_cache = {}
    job = []
    for page in sorted(per_page):
        prev_rec = None
        for e in per_page[page]:
            rec = matched.get((e["page"], e["index"]))
            scan = page_scan.get(page)
            if rec is not None:
                vol = rec["vol"]
                if vol not in pm_cache:
                    pm_cache[vol] = load_pagemap(vol) or {}
                pm = pm_cache[vol]
                if rec["pdf"] in pm:
                    scan = (vol, pm[rec["pdf"]])
            job.append({"entry": e, "rec": rec, "prev": prev_rec, "scan": scan})
            if rec is not None:
                prev_rec = rec
    # 按扫描页归并，一页只解码一次（同一册、同一页序相邻）
    job.sort(key=lambda j: (j["scan"][0], j["scan"][1], j["entry"]["page"], j["entry"]["index"])
             if j["scan"] else ("", 10 ** 9, j["entry"]["page"], j["entry"]["index"]))

    vols = {}
    cells = []
    cut_ok = cut_fail = reused = font_ok = rect_ok = page_ok = det_ok = 0
    rect_ious = []
    rect_low = 0
    cut_scores = []
    missed = []
    used_boxes = {}
    # 每个字的模板（用上游给它切的那张净字图）：补切时用来在扫描里找同一字形
    templates = {}
    sibling_file = {}
    for rec in records:
        if rec["char"] in sibling_file:
            continue
        path = os.path.join(glyph_dir, rec["file"])
        if os.path.exists(path):
            sibling_file[rec["char"]] = path
            templates[rec["char"]] = glyph_template(path)

    for j in job:
        e = j["entry"]
        rec = j["rec"]
        prev_rec = j["prev"]
        vol_idx = j["scan"]
        cell = None
        src = None
        if rec is not None and vol_idx is not None and cut_all:
            # 全部从 300dpi 扫描按该位置的矩形现裁；与上游给该位置的净字图算重合度，
            # 重合度太低说明这一页的页序对错了或者矩形落偏了 → 退回上游那张
            vol, idx = vol_idx
            if vol not in vols:
                vols[vol] = Volume(vol)
            arr = vols[vol].array(idx)
            if arr is not None:
                got = cut_rect(arr, rec)
                if got is not None:
                    path = os.path.join(glyph_dir, rec["file"])
                    if os.path.exists(path):
                        own = cell_from_glyph(path)
                        ov = overlap(got, own)
                        rect_ious.append(round(ov, 3))
                        if ov >= MIN_OVERLAP:
                            cell, src = got, "scan-rect"
                            rect_ok += 1
                    else:
                        cell, src = got, "scan-rect"
                        rect_ok += 1
        if cell is None and rec is not None:
            path = os.path.join(glyph_dir, rec["file"])
            if os.path.exists(path):
                cell = cell_from_glyph(path)
                src = "own"
        if cell is None and e_key(e) in detected and vol_idx is not None:
            # 上游没给这一处的矩形，但自动定位找到了大字 → 按该框现裁
            vol, idx = vol_idx
            if vol not in vols:
                vols[vol] = Volume(vol)
            arr = vols[vol].array(idx)
            if arr is not None:
                x0, y0, x1, y1 = detected[e_key(e)]["box"]
                got = cut_rect(arr, {"x0": x0, "y0": y0, "x1": x1, "y1": y1})
                if got is not None:
                    cell, src = got, "detect"
                    det_ok += 1
        if cell is None and prev_rec is not None and vol_idx is not None:
            vol, idx = vol_idx
            if vol not in vols:
                vols[vol] = Volume(vol)
            arr = vols[vol].array(idx)
            if arr is not None:
                got, score = cut_from_scan(arr, prev_rec, top_of.get(vol_idx, 300.0),
                                           templates.get(e["char"]))
                if got is not None:
                    cell, src = got, "scan"
                    cut_ok += 1
                    cut_scores.append(round(score, 3))
        if cell is None and e["char"] in sibling_file:
            # 这一处既没有上游矩形、整页也找不到：只能用同字那张（图上会和另一处一样）
            cell, src = cell_from_glyph(sibling_file[e["char"]]), "same-glyph"
            reused += 1
        if cell is None and prev_rec is not None and vol_idx is not None:
            # 该字连一张上游切图都没有：用参考字体模板再找一次
            vol, idx = vol_idx
            arr = vols[vol].array(idx) if vol in vols else None
            if arr is not None:
                got, score = cut_from_scan(arr, prev_rec, top_of.get(vol_idx, 300.0),
                                           font_template(e["char"]), min_score=0.62)
                if got is not None:
                    cell, src = got, "font"
                    font_ok += 1
        if cell is None:
            cut_fail += 1
            missed.append({"page": e["page"], "index": e["index"], "char": e["char"]})
            continue
        cells.append({
            "key": kx_key(e["page"], e["index"]), "page": e["page"], "index": e["index"],
            "ziid": ziid_of(e["char"]), "char": e["char"],
            "variant": e["variant"], "cell": cell, "src": src,
        })

    for v in vols.values():
        v.close()

    print("出格 %d（上游自己的图 %d / 扫描按矩形 %d / 自动定位现裁 %d / 扫描补切 %d / 整页补切 %d / 同字图 %d / 字体补切 %d；缺 %d）"
          % (len(cells), sum(1 for c in cells if c["src"] == "own"), rect_ok, det_ok, cut_ok,
             page_ok, reused, font_ok, cut_fail), flush=True)
    if report_path:
        report = {
            "entries": len(entries), "records": len(records),
            "alignment_ratio": round(ratio, 5), "matched": len(matched),
            "cells": len(cells),
            "by_src": dict(collections.Counter(c["src"] for c in cells)),
            "cut_from_scan": cut_ok, "font_cut": font_ok, "cut_failed": cut_fail,
            "detected": det_ok,
            "scan_rect": rect_ok, "scan_page": page_ok,
            "rect_overlap_median": (sorted(rect_ious)[len(rect_ious) // 2] if rect_ious else None),
            "rect_overlap_lt60": sum(1 for v in rect_ious if v < MIN_OVERLAP),
            "rect_overlap_n": len(rect_ious),
            "same_glyph_reused": reused,
            "unused_records": len(unused), "pages_with_scan": len(page_scan),
            "cut_keys": [c["key"] for c in cells if c["src"].startswith("scan")],
            "src_map": {c["key"]: c["src"] for c in cells if c["src"] != "own"},
            "cut_scores": cut_scores,
            "missed": missed,
        }
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        json.dump(report, open(report_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("报告：%s" % report_path, flush=True)
    if not write:
        return cells
    manifest = save_sheets(cells, out_dir, rows)
    path = os.path.join(out_dir, "manifest.ndjson")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for rec in manifest:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
    print("写入 %s（%d 行）" % (path, len(manifest)))
    return cells


def main():
    ap = argparse.ArgumentParser(description="内府本净字 → sprites_kx（按位置）")
    ap.add_argument("--glyphs", default=DEFAULT_GLYPHS)
    ap.add_argument("--out", default=os.path.join(ROOT, "sprites_kx"))
    ap.add_argument("--rows", type=int, default=64)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--report", default=os.path.join(CACHE, "kx-build-report.json"))
    ap.add_argument("--cut-all", action="store_true",
                    help="全部从 300dpi 扫描按位置现裁（上游净字图只当模板）")
    args = ap.parse_args()
    if not os.path.isdir(args.glyphs):
        sys.exit("找不到净字目录：%s" % args.glyphs)
    build(args.glyphs, args.out, args.rows, args.write, args.report, cut_all=args.cut_all)


if __name__ == "__main__":
    main()
