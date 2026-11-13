#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补切《汉字海》漏掉的字头：按条目号把"编号不连续"的地方找出来，再回到页面上找字头框。

思路：

1. `ziid.ndjson` 已经给每个框排好了印刷条目号，`1…102434` 里没落框的号就是漏切；
2. 每个漏掉的号都夹在前后两个已框条目之间 —— 同页就取两者的 y 之间，跨页就在
   上页该栏末尾 / 下页该栏开头；
3. 在这些空档里用**放宽的字头判据**（≥0.55 行距、只看栏左缘那一竖条）找候选；
4. 候选逐个 OCR 它自己的「条目号 + 属性行」窗口，**号码对上才采纳** —— 这样既不漏也不瞎补。

    python tools/hzh_recut.py --find          # 找候选 -> _zihai/hh/recut.ndjson
    python tools/hzh_recut.py --merge         # 把采纳的框并进 index.ndjson（先备份）
    python tools/hzh_recut.py --find --limit 40   # 只跑前 40 个有缺号的页，肉眼先看
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hzh_crop as C  # noqa: E402
import hzh_fields as F  # noqa: E402

TOTAL = 102434
MIN_PITCH = 0.55               # 候选字头最小边长（行距为单位），比正式判据 0.98 松
MAX_H = 3.80                   # 候选字头最大高度：有的字头是竖长的（丨、𠃟…），正式判据 1.85 会漏
MAX_W = 2.20


# ---------------------------------------------------------------- 数据

def load(data):
    """读 index + fields，并按 hzh_ziid 的口径（横带 + 串号）给每框排好条目号。"""
    import hzh_ziid as Z
    pages = Z.load_pages(data)
    assign_all, _stats, _notes = Z.assign_pages(pages)
    rows = {}
    for pno, rs in pages.items():
        for r, e in zip(rs, assign_all[pno][0]):
            r["entry"] = e
        rows[pno] = rs
    return rows


def page_gaps(rows, tail_to=None):
    """本页缺哪些条目号：页内空档 + 页尾到下一页首条之间的号（tail_to = 下页首条）。"""
    known = sorted(r["entry"] for r in rows if r.get("entry"))
    if not known:
        return []
    kset = set(known)
    hi = known[-1]
    if tail_to and tail_to > hi + 1:
        hi = tail_to - 1
    return [e for e in range(known[0], hi + 1) if e not in kset]


def tail_regions(rows, pitch, page_h, missing, last_col):
    """页尾漏号：从最后一框所在栏往下找，找完再往右边各栏找。"""
    known = [r for r in rows if r.get("entry")]
    if not known:
        return []
    last = max(known, key=lambda r: (r["col"], r["y"]))
    out = []
    for e in missing:
        out.append((e, last["col"] - 1, last["y"] + 0.55 * pitch, page_h))
        for ci in range(last["col"], last_col):
            out.append((e, ci, 0, page_h))
    return out


# ---------------------------------------------------------------- 找候选

def _col_heads(raw, pitch, x0, col_right, y0, y1, skip):
    """在 [y0, y1) 里找"栏左缘的够大字块"，返回候选框列表。

    这里故意放宽（0.55 行距起、允许竖长字），正文首字也会混进来 ——
    反正后面还有一道 OCR 号码验证，假候选会被刷掉。
    """
    ink = C.drop_rules(raw)
    ya, yb = int(max(0, y0)), int(max(0, y1))
    xa, xb = int(max(0, x0 - 0.5 * pitch)), int(x0 + 1.1 * pitch)
    sub = ink[ya:yb, xa:xb]
    if sub.size == 0:
        return []
    sub = cv2.morphologyEx(sub, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, _lab, st, _ = cv2.connectedComponentsWithStats(sub, connectivity=8)
    comps = []
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in st[i])
        if a < 0.02 * pitch * pitch or max(w, h) < 0.55 * pitch:
            continue
        if h > MAX_H * pitch or w > MAX_W * pitch:
            continue
        comps.append([xa + x, ya + y, w, h])
    comps.sort(key=lambda c: c[1])
    groups = []
    for c in comps:                                 # 竖向挨着的笔画并成一个字
        if groups and c[1] - (groups[-1][1] + groups[-1][3]) < 0.55 * pitch:
            p = groups[-1]
            p[2] = max(p[0] + p[2], c[0] + c[2]) - p[0]
            p[3] = max(p[1] + p[3], c[1] + c[3]) - p[1]
            continue
        groups.append(list(c))
    keep = []
    for x, y, w, h in groups:
        if any(abs(y - s) < 0.60 * pitch for s in skip):
            continue                                # 已有框的位置不算
        keep.append(dict(x=int(x), y=int(y), w=int(max(w, 1.02 * pitch)), h=int(h)))
    return keep


def _regions(rows, pitch, margins, page_h, missing, col_right_of):
    """每个缺号 → 该去哪里找：[(entry, col, y0, y1)]。"""
    out = []
    known = [r for r in rows if r.get("entry")]
    for e in missing:
        prev = max((r for r in known if r["entry"] < e), key=lambda r: r["entry"], default=None)
        nxt = min((r for r in known if r["entry"] > e), key=lambda r: r["entry"], default=None)
        if prev and nxt and prev["col"] == nxt["col"]:
            out.append((e, prev["col"] - 1, prev["y"] + 0.55 * pitch, nxt["y"] - 0.25 * pitch))
        elif prev and nxt:
            out.append((e, prev["col"] - 1, prev["y"] + 0.55 * pitch, page_h))
            out.append((e, nxt["col"] - 1, 0, nxt["y"] - 0.25 * pitch))
        elif prev:
            out.append((e, prev["col"] - 1, prev["y"] + 0.55 * pitch, page_h))
        elif nxt:
            out.append((e, nxt["col"] - 1, 0, nxt["y"] - 0.25 * pitch))
    return out


def find_page(args):
    pdf, pno, rows, missing, tail_to = args
    raw = C.load_ink(pdf, pno)
    _heads, pitch, margins, _sus = C.page_boxes(raw)
    h = raw.shape[0]
    skip = [r["y"] for r in rows]
    miss_in = [e for e in missing if any(r.get("entry") == e or (r.get("entry") or 0) > e
                                        for r in rows) and (rows[-1].get("entry") or 0) >= e]
    miss_tail = [e for e in missing if e not in miss_in]
    cands = []
    for e, ci, y0, y1 in _regions(rows, pitch, margins, h, miss_in, None):
        if ci < 0 or ci >= len(margins):
            continue
        x0 = margins[ci]
        for c in _col_heads(raw, pitch, x0, None, y0, y1, skip):
            c.update(page=pno, col=ci + 1, entry=e)
            cands.append(c)
    if miss_tail:
        for e, ci, y0, y1 in tail_regions(rows, pitch, h, miss_tail, len(margins)):
            if ci < 0 or ci >= len(margins):
                continue
            x0 = margins[ci]
            for c in _col_heads(raw, pitch, x0, None, y0, y1, skip):
                c.update(page=pno, col=ci + 1, entry=e)
                cands.append(c)
    return pno, pitch, margins, cands


# ---------------------------------------------------------------- OCR 验证

def rescan_page(args):
    """整页重扫一遍：用（摆正过的）栏位重新找字头，只留下索引里还没有的那些。

    `loose` 时把候选字头的最小边长放到 0.5 行距 —— 有的字头字形没撑满格（一笔的
    丶丿丨、半格的异体字），正式判据 0.98 会直接筛掉。
    """
    pdf, pno, rows, loose = args
    raw = C.load_ink(pdf, pno)
    old = C.HEAD_MIN_CAND
    if loose:
        C.HEAD_MIN_CAND = 0.50
    try:
        heads, pitch, margins, _sus = C.page_boxes(raw)
    finally:
        C.HEAD_MIN_CAND = old
    have = [(r["x"], r["y"]) for r in rows]
    cands = []
    for h in heads:
        if any(abs(h["x"] - x) < 0.5 * pitch and abs(h["y"] - y) < 0.5 * pitch
               for x, y in have):
            continue
        cands.append(dict(page=pno, col=h["col"] + 1, x=h["x"], y=h["y"],
                          w=h["w"], h=h["h"], entry=None))
    return pno, cands


def verify(pdf, pno, cands, accept=None, onnx_threads=2):
    """把候选的「条目号 + 属性行」窗口拼一条长图 OCR，读出号码。"""
    F.limit_onnx_threads(onnx_threads)
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()
    for op in ocr.text_detector.preprocess_op:
        if hasattr(op, "limit_side_len"):
            op.limit_side_len = F.DET_LIMIT
    raw = C.load_ink(pdf, pno).astype(np.float32)
    _heads, pitch, margins, _sus = C.page_boxes(raw.astype(np.uint8))
    items = []
    for c in cands:
        ci = c["col"] - 1
        x0 = c.get("x") or margins[ci]
        right = int(raw.shape[1] - 1) if ci + 1 >= len(margins) else int(margins[ci + 1] - 0.3 * pitch)
        items.append((int(x0 + 0.95 * pitch), int(max(0, c["y"] - 0.70 * pitch)),
                      int(min(raw.shape[0], c["y"] + 1.60 * pitch)), right))
    texts = F.ocr_strip(ocr, raw, items, pitch)
    out = []
    for c, blocks in zip(cands, texts):
        num = F.parse_entry_num(blocks, pitch)
        c["ocr_entry"] = num
        if c.get("entry") is not None:
            c["ok"] = num == c["entry"]
        else:
            c["ok"] = bool(accept) and num in accept
            if c["ok"]:
                c["entry"] = num
        out.append(c)
    return out


def _num_from_text(txt):
    """把一段 OCR 文本当条目号读（要去掉前后的杂字）。"""
    t = "".join(ch for ch in (txt or "") if ch in "0123456789")
    if not t or len(t) > 6:
        return None
    return int(t)


def _rule_rows(raw, x0, x1, y0, y1):
    """栏内通栏横线的行号（书页顶/底那条分隔线）。"""
    xs0, xs1 = int(max(0, x0)), int(min(raw.shape[1], x1))
    if xs1 - xs0 < 8:
        return []
    seg = raw[int(max(0, y0)):int(min(raw.shape[0], y1)), xs0:xs1]
    if not seg.size:
        return []
    return [int(max(0, y0)) + i for i in np.nonzero(seg.sum(axis=1) > 0.6 * (xs1 - xs0))[0]]


def scan_numbers(pdf, pno, rows, missing, pitch_hint=None, onnx_threads=2):
    """按号码找框：在缺号该出现的位置 OCR 整窗，**看到那个号码就地在号码左边建框**。

    不靠"字头长什么样"，只靠"书上印的号在哪儿" —— 竖长字头、一笔字（丶丿丨）、
    被正文挤扁的字头都能救回来。
    """
    raw8 = C.load_ink(pdf, pno)
    _heads, pitch, margins, _sus = C.page_boxes(raw8)
    raw = raw8.astype(np.float32)
    H = raw8.shape[0]
    known = [r for r in rows if r.get("entry")]
    items, want = [], []
    for e in missing:
        prev = max((r for r in known if r["entry"] < e), key=lambda r: r["entry"], default=None)
        nxt = min((r for r in known if r["entry"] > e), key=lambda r: r["entry"], default=None)
        ci = (prev or nxt)["col"] - 1
        if prev and nxt and prev["col"] == nxt["col"]:
            col, y0, y1 = prev["col"], prev["y"] + 0.5 * pitch, nxt["y"] - 0.2 * pitch
        elif prev:
            # 缺号在本栏末尾（或本页最后一栏）：一直到栏底那条横线
            col = prev["col"]
            ci2 = col
            right0 = int(margins[ci2]) if ci2 < len(margins) else 0
            right1 = int(margins[ci2 + 1]) if ci2 + 1 < len(margins) else raw8.shape[1]
            rules = [y for y in _rule_rows(raw8, right0, right1, prev["y"] + pitch, H)
                     if y > prev["y"] + 1.2 * pitch]
            y0 = prev["y"] + 0.5 * pitch
            y1 = (min(rules) - 2) if rules else int(H * 0.975)
        elif nxt:
            # 缺号在本栏开头（或本页第一栏）：从栏顶那条横线往下 —— 不能只往上找 6 行距，
            # 长释义的条目会把真正的字头甩到窗口外面（p630 的 8322 就这么丢的）
            col = nxt["col"]
            ci2 = col
            left0 = int(margins[ci2 - 1]) if ci2 - 1 < len(margins) else 0
            left1 = int(margins[ci2]) if ci2 < len(margins) else raw8.shape[1]
            rules = [y for y in _rule_rows(raw8, left0, left1, 0, nxt["y"])
                     if y < nxt["y"] - 1.2 * pitch]
            y0 = (max(rules) + 2) if rules else int(H * 0.045)
            y1 = nxt["y"] - 0.2 * pitch
        else:
            continue
        ci = col - 1
        if ci < 0 or ci >= len(margins):
            continue
        x0 = int(margins[ci] + 0.6 * pitch)
        right = int(margins[ci + 1] - 0.3 * pitch) if ci + 1 < len(margins) else int(raw8.shape[1] * 0.98)
        items.append((x0, int(max(0, y0)), int(min(raw8.shape[0], y1)), right))
        want.append((e, ci, x0, int(max(0, y0))))
    if not items:
        return []
    F.limit_onnx_threads(onnx_threads)
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()
    for op in ocr.text_detector.preprocess_op:
        if hasattr(op, "limit_side_len"):
            op.limit_side_len = F.DET_LIMIT
    blocks = F.ocr_strip(ocr, raw, items, pitch, with_box=True)
    out = []
    for (e, ci, x0, wy0), blks in zip(want, blocks):
        hit = None
        for b in blks:
            num = _num_from_text(b["text"])
            if num == e:
                hit = b
                break
        if hit is None:
            continue
        y = wy0 + hit["y"] - 0.45 * pitch
        side = 1.02 * pitch
        out.append(dict(page=pno, col=ci + 1, x=int(margins[ci]), y=int(max(0, y)),
                        w=int(side), h=int(side), entry=e, ocr_entry=e, ok=True,
                        src="by-number"))
    return out


def scan_page_numbers(pdf, pno, rows, missing, onnx_threads=2):
    """整页找号：把每一栏自上而下切成一窗窗（4 行距高、1 行距重叠）OCR，见到待补的号就地建框。

    比 `scan_numbers` 更粗放但更保险：不猜"这个号该落在哪一行"，把整页所有号码读一遍，
    号在哪儿就在它左边定框 —— 号在栏顶、栏底、甚至跑到下一页的栏顶都不怕。
    """
    raw8 = C.load_ink(pdf, pno)
    ink = C.drop_rules(raw8)
    raw = raw8.astype(np.float32)
    H, W = raw8.shape
    pitch = float(rows[0].get("pitch") or 91) if rows else 91.0
    margins = {}
    for r in rows:
        margins[r["col"]] = min(margins.get(r["col"], W), r["x"])
    if not margins:
        return []
    step = int(3.2 * pitch)
    y0, y1 = int(H * 0.030), int(H * 0.985)
    items, meta = [], []
    for col in sorted(margins):
        xa = int(max(0, margins[col] - 0.40 * pitch))
        xb = int(min(W, margins[col] + 5.00 * pitch))
        y = y0
        while y < y1:
            yb = min(y1, y + int(4.2 * pitch))
            items.append((xa, y, yb, xb))
            meta.append((col, xa, y))
            if yb >= y1:
                break
            y += step
    if not items:
        return []
    F.limit_onnx_threads(onnx_threads)
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()
    for op in ocr.text_detector.preprocess_op:
        if hasattr(op, "limit_side_len"):
            op.limit_side_len = F.DET_LIMIT
    blocks = F.ocr_strip(ocr, raw, items, pitch, with_box=True)
    out, seen = [], set()
    for (col, wx0, wy0), blks in zip(meta, blocks):
        for b in blks:
            num = _num_from_text(b["text"])
            if num is None or num not in missing or num in seen:
                continue
            seen.add(num)
            box = _head_box_left_of_number(
                ink, margins[col], (wx0 + b["x"], wy0 + b["y"], b["w"], b["h"]), pitch)
            out.append(dict(page=pno, col=col, x=box[0], y=box[1], w=box[2], h=box[3],
                            entry=num, ocr_entry=num, ok=True, src="page-number"))
    return out


def _head_box_left_of_number(ink, left, num_box, pitch):
    """号码左边的那个字头：和号码同一行、靠栏左缘的那一块墨。

    `num_box` = 号码在整页坐标里的 (x, y, w, h)。字头不一定和号码等高（一笔字很小），
    所以按"连通块中心离号码中线最近、且不越过栏左缘太多"来挑。
    """
    nx, ny, nw, nh = num_box
    ncy = ny + nh / 2.0
    x0 = int(max(0, left - 0.20 * pitch))
    x1 = int(min(ink.shape[1], nx - 0.02 * pitch))
    y0 = int(max(0, ncy - 1.10 * pitch))
    y1 = int(min(ink.shape[0], ncy + 1.10 * pitch))
    fallback = (int(left), int(max(0, ncy - 0.51 * pitch)),
                int(1.02 * pitch), int(1.02 * pitch))
    sub = ink[y0:y1, x0:x1]
    if sub.size == 0 or not sub.any():
        return fallback
    n, _lab, st, _cent = cv2.connectedComponentsWithStats(sub, connectivity=8)
    best, best_key = None, None
    for i in range(1, n):
        bx, by, bw, bh, area = st[i]
        if area < 25:
            continue
        cy = y0 + by + bh / 2.0
        if by + y0 > ncy + 0.75 * pitch or by + bh + y0 < ncy - 0.75 * pitch:
            continue                                     # 不是这一行的
        key = (abs(cy - ncy), -area)
        if best_key is None or key < best_key:
            best, best_key = (bx, by, bw, bh), key
    if best is None:
        return fallback
    bx, by, bw, bh = best
    a = int(0.06 * pitch)
    return (max(0, x0 + bx - a), max(0, y0 + by - a),
            int(bw + 2 * a), int(bh + 2 * a))


def _runs(mask, gap):
    """mask 里连成片的 True 段（间隔 ≤ gap 的并成一段）。"""
    idx = np.nonzero(mask)[0]
    if not len(idx):
        return []
    out = [[int(idx[0]), int(idx[0])]]
    for i in idx[1:]:
        if i - out[-1][1] - 1 <= gap:
            out[-1][1] = int(i)
        else:
            out.append([int(i), int(i)])
    return out


def tighten_rows(rows, ink):
    """把每个框收紧到实际墨迹，但**先向左放宽再找墨迹**。

    很多字头不是一整块：左边还有独立的一笔/一竖（協 = 十+劦、刿 = 两半刂），按字格给框时
    左半边会被切在框外。所以收紧时把搜索窗向左撑 0.55 行距（向右只撑 0.10，免得把右上角的
    条目号吞进来），纵向只留 0.12 行距（上下相邻的是释义行，撑大了会把下一行框进来）。
    """
    H, W = ink.shape
    out = []
    for r in rows:
        p = float(r.get("pitch") or 91)
        x0, y0 = int(r["x"]), int(r["y"])
        x1, y1 = x0 + int(r["w"]), y0 + int(r["h"])
        wx0, wx1 = max(0, int(x0 - 0.55 * p)), min(W, int(x1 + 0.10 * p))
        wy0, wy1 = max(0, int(y0 - 0.12 * p)), min(H, int(y1 + 0.12 * p))
        sub = ink[wy0:wy1, wx0:wx1]
        if not sub.size:
            out.append(r)
            continue
        sel = np.zeros(sub.shape[1], bool)
        for a, b in _runs(sub.sum(axis=0) > 0, int(0.25 * p)):
            if b >= x0 - wx0 - 1 and a <= x1 - wx0 + 1:     # 和原框沾边的墨迹才算同一个字
                sel[a:b + 1] = True
        rr = [s for s in _runs(sub[:, sel].sum(axis=1) > 0, int(0.15 * p))
              if s[1] >= y0 - wy0 - 1 and s[0] <= y1 - wy0 + 1]
        if not sel.any() or not rr:
            out.append(r)
            continue
        xs = np.nonzero(sel)[0]
        a = int(0.06 * p)                                   # 留一点白边，字形不贴框
        nx0, nx1 = wx0 + xs[0], wx0 + xs[-1] + 1
        ny0, ny1 = wy0 + rr[0][0], wy0 + rr[-1][1] + 1
        nx0, ny0 = max(0, nx0 - a), max(0, ny0 - a)
        nx1, ny1 = min(W, nx1 + a), min(H, ny1 + a)
        if nx1 - nx0 >= 8 and ny1 - ny0 >= 8:
            r = dict(r, x=int(nx0), y=int(ny0), w=int(nx1 - nx0), h=int(ny1 - ny0))
        out.append(r)
    return out


def _ink_mass(ink, r):
    sub = ink[r["y"]:r["y"] + r["h"], r["x"]:r["x"] + r["w"]]
    return int(sub.sum()) if sub.size else 0


def dedupe_rows(rows, ink=None):
    """同一行上互相重叠的两块，多半是同一个字头被切成了两半（刿 = 刂+刂）。

    照片上这些页的字头是"左密右疏"的：一块框住整个字、另一块整体右移了约一个字宽、
    右边压在空白上（旧版栏位算歪时留下的）。所以按墨量判断：
    - 次块的墨迹几乎全被主块盖住（多出来的不到主块 30%）→ 是重复框，直接丢掉；
    - 两块各有各的墨（真要拼才成形）→ 取并集。
    并完从"有号的那一块"继承派生字段（entry/obs/部首…），行号重排。
    """
    n = len(rows)
    if n < 2:
        return rows, 0
    p = float(rows[0].get("pitch") or 91)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n):
        a = rows[i]
        for j in range(i + 1, n):
            b = rows[j]
            mid = 0.5 * max(a["h"], b["h"])
            if abs((a["y"] + a["h"] / 2) - (b["y"] + b["h"] / 2)) > 0.70 * mid:
                continue
            ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            if ox <= 0 or oy <= 0.5 * min(a["h"], b["h"]):
                continue
            ra, rb = find(i), find(j)
            if ra != rb:
                parent[rb] = ra
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out, removed = [], 0
    for key in sorted(groups):
        g = [rows[i] for i in groups[key]]
        if len(g) == 1:
            out.append(g[0])
            continue
        keep = g
        if ink is not None:
            g = sorted(g, key=lambda r: -_ink_mass(ink, r))
            best = g[0]
            bx0, by0 = best["x"], best["y"]
            bx1, by1 = bx0 + best["w"], by0 + best["h"]
            cover = ink[by0:by1, bx0:bx1]
            bm = int(cover.sum()) or 1
            extra = 0
            for r in g[1:]:
                sub = ink[r["y"]:r["y"] + r["h"], r["x"]:r["x"] + r["w"]]
                ex = ink[max(r["y"], by0):min(r["y"] + r["h"], by1),
                         max(r["x"], bx0):min(r["x"] + r["w"], bx1)]
                extra += int(sub.sum()) - int(ex.sum())
            if extra < 0.30 * bm:                        # 次块的墨基本都在主块里 → 重复框
                keep = [best]
            else:
                keep = g
        base = next((r for r in keep if r.get("entry")), keep[0])
        x0 = min(r["x"] for r in keep)
        y0 = min(r["y"] for r in keep)
        x1 = max(r["x"] + r["w"] for r in keep)
        y1 = max(r["y"] + r["h"] for r in keep)
        out.append(dict(base, x=int(x0), y=int(y0), w=int(x1 - x0), h=int(y1 - y0)))
        removed += len(g) - 1
    out.sort(key=lambda r: (r["col"], r["y"]))
    for i, r in enumerate(out, 1):
        r["seq"] = i
    if ink is not None:
        out = _drop_shadow(out, ink)
    return out, removed


def _drop_shadow(rows, ink):
    """同一行上两个框、又都读出了同一个号：留下墨更密（更贴字形）的那个。

    这种框是早期按"字格"或算歪的栏位放的，一个框住整个字、另一个横跨两三行。
    """
    n = len(rows)
    drop = set()
    for i in range(n):
        for j in range(i + 1, n):
            a, b = rows[i], rows[j]
            if not a.get("entry") or a.get("entry") != b.get("entry"):
                continue
            ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            if ox <= 0 or oy <= 0:
                continue
            if i in drop or j in drop:
                continue
            da = _ink_mass(ink, a) / float(a["w"] * a["h"])
            db = _ink_mass(ink, b) / float(b["w"] * b["h"])
            drop.add(j if da >= db else i)
    if not drop:
        return rows
    out = [r for k, r in enumerate(rows) if k not in drop]
    for i, r in enumerate(out, 1):
        r["seq"] = i
    return out


def clean_page(args):
    """一页之内：先并掉重复/拼块框，再收紧。"""
    pdf, pno, rows = args
    ink = C.drop_rules(C.load_ink(pdf, pno))
    out, _ = dedupe_rows(rows, ink)
    return pno, tighten_rows(out, ink), len(rows) - len(out)


def tighten_page(args):
    """只收紧、不并框（--tighten 单独跑时用）。"""
    pdf, pno, rows = args
    ink = C.drop_rules(C.load_ink(pdf, pno))
    return pno, tighten_rows(rows, ink)


# ---------------------------------------------------------------- 合并

def merge(data, accepted):
    path = os.path.join(data, "index.ndjson")
    rows_by_page = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            o = json.loads(line)
            rows_by_page.setdefault(o["page"], []).append(o)
    add = {}
    for c in accepted:
        add.setdefault(c["page"], []).append(c)
    for pno, extra in add.items():
        rows = rows_by_page.setdefault(pno, [])
        for c in extra:
            rows.append(dict(page=pno, seq=0, col=c["col"], x=c["x"], y=c["y"],
                             w=c["w"], h=c["h"], pitch=c.get("pitch", 91)))
        rows.sort(key=lambda r: (r["col"], r["y"]))
        for i, r in enumerate(rows, 1):
            r["seq"] = i
    bak = path + ".bak"
    if not os.path.exists(bak):
        os.replace(path, bak)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for pno in sorted(rows_by_page):
            for r in rows_by_page[pno]:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return sum(len(v) for v in add.values())


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--data", default=os.path.join(here, "_zihai", "hh"))
    ap.add_argument("--pdf", default=C.PDF)
    ap.add_argument("--find", action="store_true")
    ap.add_argument("--rescan", action="store_true",
                    help="整页按摆正后的栏位重扫（收拾整栏没切出来的页）")
    ap.add_argument("--loose", action="store_true",
                    help="配合 --rescan：候选字头最小边长放宽到 0.5 行距（字形没撑满格的）")
    ap.add_argument("--by-number", action="store_true",
                    help="按缺号 OCR 整窗，看到号码就在号码左边建框")
    ap.add_argument("--page-numbers", action="store_true",
                    help="整页找号：每栏字头+号码那条竖带整条 OCR，见到待补号就地建框")
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--tighten", action="store_true",
                    help="把所有框收紧到实际墨迹（向左放宽 0.55 行距找左半边墨迹，留 6% 白边）")
    ap.add_argument("--dedupe", action="store_true",
                    help="把同一行上重叠的两块（一个字头被切成两半）并成一个框")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个有缺号的页")
    ap.add_argument("--jobs", type=int, default=min(12, max(1, (os.cpu_count() or 2) // 2)))
    args = ap.parse_args()

    idx = load(args.data)

    path = os.path.join(args.data, "index.ndjson")
    if args.dedupe or args.tighten:
        todo_pages = sorted(p for p in idx if idx[p])
        removed = 0
        if args.dedupe:                       # 并框 + 收紧要读同一张图，一起做
            with ProcessPoolExecutor(max_workers=args.jobs) as ex:
                res = list(ex.map(clean_page, [(args.pdf, p, idx[p]) for p in todo_pages]))
            for pno, rows, n in res:
                idx[pno] = rows
                removed += n
            print("拼块/重复框合并：并掉 %d 个框" % removed)
        else:
            with ProcessPoolExecutor(max_workers=args.jobs) as ex:
                res = list(ex.map(tighten_page, [(args.pdf, p, idx[p]) for p in todo_pages]))
            for pno, rows in res:
                idx[pno] = rows
        # 旧框不收紧了：clean/tighten 都是按当前墨迹重算，但"格"→"墨迹"的判断要基于
        # 上一轮的格框，所以收紧过的框再来一次也不会越收越小（见 tighten_rows 的搜索窗）
        for i in (2, 3):
            if not os.path.exists("%s.bak%d" % (path, i)):
                with open(path, "rb") as a, open("%s.bak%d" % (path, i), "wb") as b:
                    b.write(a.read())
                break
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            for pno in sorted(idx):
                for r in idx[pno]:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print("框数 %d" % sum(len(v) for v in idx.values()))
        return

    pages_sorted = sorted(idx)
    first_of = {}
    for pno in pages_sorted:
        ents = [r["entry"] for r in idx[pno] if r.get("entry")]
        if ents:
            first_of[pno] = min(ents)
    todo = []
    for pno in pages_sorted:
        nxt = next((first_of[p] for p in pages_sorted if p > pno and p in first_of), None)
        miss = page_gaps(idx[pno], nxt)
        if miss:
            todo.append((pno, miss, nxt))
    print(f"有缺号的页 {len(todo)} 个，缺 {sum(len(m) for _p, m, _n in todo)} 个号")
    if args.limit:
        todo = todo[:args.limit]
        print(f"（只处理前 {len(todo)} 页）")

    if args.page_numbers:
        # 整页找号：缺号的页和它的左右邻页都扫（号可能印在栏顶/栏底，甚至翻页那边）
        out_path = os.path.join(args.data, "recut_pn.ndjson")
        want = {}
        for pno, miss, _n in todo:
            for p in (pno - 1, pno, pno + 1):
                if idx.get(p):
                    want.setdefault(p, set()).update(miss)
        jobs = [(args.pdf, p, idx[p], sorted(ms)) for p, ms in sorted(want.items())]
        found, seen = [], set()
        with ProcessPoolExecutor(max_workers=max(1, args.jobs // 2)) as ex:
            for res in ex.map(_scan_page_one, jobs, chunksize=1):
                for c in res:
                    if c["entry"] in seen:
                        continue
                    seen.add(c["entry"])
                    found.append(c)
        print(f"整页找号：扫 {len(jobs)} 页，找回 {len(found)}/"
              f"{sum(len(m) for _p, m, _n in todo)} 个")
        with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            for c in found:
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")
        print("->", out_path)
        return

    if args.by_number:
        out_path = os.path.join(args.data, "recut_bn.ndjson")
        found = []
        jobs = [(args.pdf, pno, idx[pno], miss) for pno, miss, _n in todo]
        # 页尾缺的那个号常常其实是**下一页栏顶**那个没撑满格的字头（p492 的 1845 就是：
        # 上页末尾什么都没有，号在下页栏顶、字形只有三小笔），所以下一页也扫一遍
        jobs += [(args.pdf, pno + 1, idx[pno + 1], miss)
                 for pno, miss, _n in todo if idx.get(pno + 1)]
        seen = set()
        for res in _scan_all(jobs, args.jobs):
            for c in res:
                if c["entry"] in seen:
                    continue
                seen.add(c["entry"])
                found.append(c)
        print(f"按号码找回 {len(found)}/{sum(len(m) for _p, m, _n in todo)} 个")
        with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            for c in found:
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")
        print("->", out_path)
        return

    if args.rescan:
        # 全书还缺的号（任何页上出现这些号才收）
        want = set()
        for _p, miss, _n in todo:
            want.update(miss)
        print(f"全书待补号 {len(want)} 个，扫 {len(todo)} 页")
        out_path = os.path.join(args.data, "recut_rs.ndjson")
        cands_all = []
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            jobs = [(args.pdf, pno, idx[pno], args.loose) for pno, _m, _n in todo]
            for pno, cands in ex.map(rescan_page, jobs, chunksize=1):
                cands_all.extend(cands)
        print(f"新候选框 {len(cands_all)} 个")
        by_page = {}
        for c in cands_all:
            by_page.setdefault(c["page"], []).append(c)
        jobs = [(args.pdf, p, by_page[p], want) for p in sorted(by_page)]
        verified = []
        with ProcessPoolExecutor(max_workers=max(1, args.jobs // 2)) as ex:
            for res in ex.map(_verify_scan, jobs, chunksize=1):
                verified.extend(res)
        ok = [c for c in verified if c["ok"]]
        print(f"OCR 验证：{len(ok)}/{len(verified)} 个号码在待补号里")
        with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            for c in ok:
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")
        print("->", out_path)
        return

    if args.find:
        out_path = os.path.join(args.data, "recut.ndjson")
        cands_all = []
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            jobs = [(args.pdf, pno, idx[pno], miss, nxt) for pno, miss, nxt in todo]
            for pno, _pitch, _margins, cands in ex.map(find_page, jobs, chunksize=1):
                if cands:
                    cands_all.extend(cands)
        print(f"候选框 {len(cands_all)} 个")
        # 逐页 OCR 验证（每页一条长图）
        by_page = {}
        for c in cands_all:
            by_page.setdefault(c["page"], []).append(c)
        jobs = [(args.pdf, p, by_page[p]) for p in sorted(by_page)]
        verified = []
        with ProcessPoolExecutor(max_workers=max(1, args.jobs // 2)) as ex:
            for res in ex.map(_verify_one, jobs, chunksize=1):
                verified.extend(res)
        ok = [c for c in verified if c["ok"]]
        print(f"OCR 验证：{len(ok)}/{len(verified)} 个号码对上")
        with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            for c in ok:
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")
        stat = Counter("对" if c["ok"] else "错" for c in verified)
        print("->", out_path, dict(stat))
        return

    if args.merge:
        # 三个补切途径各有各的产物，一起并（--find / --by-number / --rescan --loose）
        paths = sorted(glob.glob(os.path.join(args.data, "recut*.ndjson")))
        accepted = []
        for p in paths:
            with open(p, encoding="utf-8") as fh:
                accepted.extend(json.loads(line) for line in fh if line.strip())
        if not accepted:
            sys.exit(f"{args.data} 下没有 recut*.ndjson，先跑 --find / --by-number / --rescan")
        print("读入 " + "、".join(os.path.basename(p) for p in paths))
        # 一个号只补一个框：同一号有多个候选时留最矮的那个（越高越可能是正文串行）
        best = {}
        for c in accepted:
            if c["entry"] not in best or c["h"] < best[c["entry"]]["h"]:
                best[c["entry"]] = c
        n = merge(args.data, list(best.values()))
        print(f"并入 {n} 个框（原 index 备份为 index.ndjson.bak）")
        return

    ap.print_help()


def _verify_one(job):
    pdf, pno, cands = job
    return verify(pdf, pno, cands)


def _verify_scan(job):
    pdf, pno, cands, want = job
    return verify(pdf, pno, cands, accept=want)


def _scan_one(job):
    pdf, pno, rows, missing = job
    return scan_numbers(pdf, pno, rows, missing)


def _scan_page_one(job):
    pdf, pno, rows, missing = job
    return scan_page_numbers(pdf, pno, rows, missing)


def _scan_all(jobs, jobs_n):
    with ProcessPoolExecutor(max_workers=max(1, jobs_n // 2)) as ex:
        for res in ex.map(_scan_one, jobs, chunksize=1):
            yield res


if __name__ == "__main__":
    main()
