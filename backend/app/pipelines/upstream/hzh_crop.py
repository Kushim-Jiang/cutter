#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在《汉字海》扫描 PDF 里定位每个字条目的字头框，只记坐标，不出图。

版面（三栏）：字头大字贴在每栏左缘，右侧紧跟条目序号；序号下面的属性行
（「一部 3画 ①4E0B」）缩进到序号那一列，释义行回到左缘。靠"栏左缘的大字号墨迹
+ 右侧有数字序号"定位，比按字号猜稳得多。

    ┌──────────────────────┐
    │ 广      9            │  ← 字头 + 序号
    │   一部 2画 ①4E06      │  ← 属性行（缩进）
    │   音未详，部首"广"的异体。│
    └──────────────────────┘

产物（都在 <out> 下，不入库）：
    index.ndjson        每框一行 {page, seq, col, x, y, w, h, pitch}
    suspects.ndjson     疑似漏切的位置（有缩进的属性行、上面却没有字头框）
    page_counts.ndjson  每页框数

用法：
    python tools/hzh_crop.py                             # 正文全量 452–2648
    python tools/hzh_crop.py --start 452 --limit 20      # 只跑前 20 页
    python tools/hzh_crop.py --debug _zihai/hh_dbg       # 另存叠了框的页图，便于目视检查
"""
import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor

import cv2
import numpy as np

PDF = ""  # 路径由 config.local.yaml / 调用方给出
FIRST_PAGE, LAST_PAGE = 452, 2648      # 正文 = PDF 452–2648 = 印刷 1–2197
HEAD_MIN = 0.98                        # 字头最小边长，单位 = 行距 pitch
HEAD_MIN_CAND = HEAD_MIN               # 候选字头的最小边长（补切时可以放宽）
HEAD_MAX = 1.85


# ---------------------------------------------------------------- 图像

def load_ink(pdf, pno):
    """取页面内嵌的原始 1bit 扫描图（不重采样），返回 ink 掩码（1 = 墨）。"""
    import fitz
    doc = fitz.open(pdf)
    try:
        page = doc[pno - 1]
        info = doc.extract_image(page.get_images(full=True)[0][0])
    finally:
        doc.close()
    arr = cv2.imdecode(np.frombuffer(info["image"], np.uint8), cv2.IMREAD_GRAYSCALE)
    ink = (arr > 0).astype(np.uint8)
    if ink.mean() > 0.5:
        ink = 1 - ink
    return ink


def drop_rules(ink):
    """去掉栏缝那几条长直线：它们会让"栏左缘""右侧留白"的判断全乱。"""
    H, W = ink.shape
    v = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(5, int(0.025 * H)))))
    h = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, int(0.04 * W)), 1)))
    out = ink.copy()
    out[v > 0] = 0
    out[h > 0] = 0
    out[out.sum(axis=1) > W * 0.30, :] = 0
    out[:, out.sum(axis=0) > H * 0.30] = 0
    return out


# ---------------------------------------------------------------- 度量

def line_pitch(st, ink):
    """行距：全书扫描分辨率一致，用正文字号的众数反推（正文高 ≈ 0.757 行距）。

    按行首间距估行距，在"每栏行距不匀"的页（分部首、笔画数标题的那些）会跑偏，
    字号却全页稳定，所以以字号为准。
    """
    hs = [int(s[3]) for s in st if 40 <= s[3] <= 115 and 40 <= s[2] <= 115 and s[4] >= 1200]
    if len(hs) >= 20:
        return float(np.median(hs) / 0.757)
    H = ink.shape[0]
    return float(H * 0.015)


def column_bounds(ink, pitch):
    """三栏的左右边界：在中部两个搜索窗里各找竖直投影最低处。"""
    H, W = ink.shape
    prof = ink[int(H * 0.06):int(H * 0.96), :].sum(axis=0).astype(float)
    prof = np.convolve(prof, np.ones(max(3, int(pitch / 6))), mode="same")
    w1 = (int(W * 0.25), int(W * 0.42))
    w2 = (int(W * 0.56), int(W * 0.74))
    g1 = w1[0] + int(np.argmin(prof[w1[0]:w1[1]]))
    g2 = w2[0] + int(np.argmin(prof[w2[0]:w2[1]]))
    return [(int(W * 0.02), g1), (g1, g2), (g2, int(W * 0.98))]


def column_margin(ink, x0, x1, ys, ye):
    """栏的正文左边距：各行最左侧墨迹 x 的众数（字头少时才用）。"""
    sub = ink[ys:ye, x0:x1]
    step = max(1, int(0.25 * (ye - ys) / 60))
    lefts = []
    for y in range(0, sub.shape[0] - step, step):
        cols = np.nonzero(sub[y:y + step].sum(axis=0) > 1)[0]
        if len(cols):
            lefts.append(int(cols[0]))
    if not lefts:
        return x0
    hist = np.bincount(np.array(lefts), minlength=x1 - x0)
    sm = np.convolve(hist, np.ones(5), mode="same")
    return int(np.argmax(sm)) + x0


# ---------------------------------------------------------------- 找字头

def _pick_glyph(seed, near, pitch):
    """从 seed 出发，把同一个字被拆开的笔画并进来（只并横向重叠、竖向贴着的）。"""
    u = list(seed)
    changed = True
    while changed:
        changed = False
        for c in near:
            if c is seed:
                continue
            ox = min(u[0] + u[2], c[0] + c[2]) - max(u[0], c[0])
            if ox < 0.45 * min(u[2], c[2]):
                continue
            gap = max(u[1] - (c[1] + c[3]), c[1] - (u[1] + u[3]), 0)
            if gap > 0.30 * pitch:
                continue
            nu = [min(u[0], c[0]), min(u[1], c[1]),
                  max(u[0] + u[2], c[0] + c[2]) - min(u[0], c[0]),
                  max(u[1] + u[3], c[1] + c[3]) - min(u[1], c[1])]
            if nu[2] > HEAD_MAX * pitch or nu[3] > 1.9 * pitch:
                continue
            if nu != u:
                u = nu
                changed = True
    return u


def _blank_right(ink, x0, y0, y1, x1):
    """(x0..x1, y0..y1) 这块是不是基本空白（只允许零星噪点）。"""
    x0, y0, x1, y1 = int(max(0, x0)), int(max(0, y0)), int(x1), int(y1)
    if x1 - x0 < 0.3 * (y1 - y0) or y1 <= y0:
        return True
    sub = ink[y0:y1, x0:x1]
    return sub.sum() <= 0.004 * sub.size + 40


def _meta_indent(ink, x0, meta_top, col_right, pitch):
    """属性行是否缩进（从序号那一列开始）；正文行不缩进。"""
    y0 = int(meta_top + 0.02 * pitch)
    y1 = int(meta_top + 0.80 * pitch)
    if y1 <= y0:
        return False
    row = ink[max(0, y0):y1, int(x0):int(col_right)]
    if row.size == 0:
        return False
    cols = np.nonzero(row.sum(axis=0) > 1)[0]
    if not len(cols):
        return False
    return 1.10 * pitch <= cols[0] <= 3.30 * pitch


def _column_margins(ink, pitch, bounds, ys, ye, allcomps):
    """各栏字头那一列的左边距。

    第三栏常常很稀（小节末尾只有两三条），按栏自己统计会跑偏 —— 这本书的栏距是固定的，
    所以只对"凑不出足够大字号样本、只能退回正文左边距"的栏，用前两栏的间距把它摆正。
    """
    margins, sure = [], []
    for bx0, bx1 in bounds:
        big = [c for c in allcomps if bx0 <= c[0] < bx1
               and HEAD_MIN * pitch <= max(c[2], c[3]) <= 1.9 * pitch
               and c[2] <= HEAD_MAX * pitch and min(c[2], c[3]) >= 0.15 * pitch]
        if len(big) >= 3:
            xs = np.array([c[0] for c in big])
            binw = max(3, int(0.15 * pitch))
            hist = np.bincount(xs // binw)
            # 字头贴在栏左缘，所以取"从左数第一个够大的簇"，别取最密的簇
            # —— 栏里那些居中的「二十五画」大标题也是大字，会跑到右边去
            need = max(3, int(0.10 * len(big)))
            b = next((i for i in range(len(hist)) if hist[i] >= need), int(np.argmax(hist)))
            margins.append(int(np.median(xs[(xs // binw) == b])))
            sure.append(True)
        else:
            margins.append(column_margin(ink, bx0, bx1, ys, ye))
            sure.append(False)
    if len(margins) >= 3 and sure[0] and sure[1] and not sure[2]:
        step = margins[1] - margins[0]
        if step > 3 * pitch:
            margins[2] = margins[1] + step
    return margins


def find_heads(ink, pitch, bounds, ys, ye, ink_c, st):
    """返回 (heads, margins, suspects)。"""
    allcomps = []
    for i in range(1, len(st)):
        x, y, w, h, a = (int(v) for v in st[i])
        if y < ys - 0.6 * pitch or y + h > ye + 0.6 * pitch:
            continue
        if a < 0.01 * pitch * pitch:
            continue
        if min(w, h) <= 0.05 * pitch and max(w, h) >= 0.7 * pitch:
            continue                                  # 栏线、脏边那种细长条
        allcomps.append([x, y, w, h])

    heads, suspects = [], []
    margins = _column_margins(ink, pitch, bounds, ys, ye, allcomps)
    for ci, (bx0, bx1) in enumerate(bounds):
        x0 = margins[ci]
        col_right = int(bx1 - 0.3 * pitch)

        strip = [c for c in allcomps if x0 - 0.6 * pitch <= c[0] <= x0 + 2.6 * pitch]
        nums = [c for c in allcomps if x0 + 1.55 * pitch <= c[0] <= x0 + 3.9 * pitch
                and 0.28 * pitch <= c[3] <= 0.62 * pitch
                and 0.04 * pitch <= c[2] <= 0.55 * pitch]
        cands = [c for c in strip if x0 - 0.45 * pitch <= c[0] <= x0 + 0.95 * pitch
                 and max(c[2], c[3]) >= HEAD_MIN_CAND * pitch
                 and c[2] <= HEAD_MAX * pitch and c[3] <= 1.9 * pitch]

        found = []
        for c in sorted(cands, key=lambda c: c[1]):
            if any(abs(c[1] - f[1]) < 0.55 * pitch for f in found):
                continue
            near = [o for o in strip if o[1] < c[1] + c[3] + 1.1 * pitch
                    and o[1] + o[3] > c[1] - 1.1 * pitch]
            g = _pick_glyph(c, near, pitch)
            x, y, w, h = g
            # 序号：字头右上角的小数字块（认不出序号的，多半不是字头）
            num = [o for o in nums if o[1] < y + 0.55 * pitch and o[1] + o[3] > y - 0.85 * pitch]
            if not num:
                if max(w, h) >= 1.2 * pitch and min(w, h) >= 0.35 * pitch:
                    suspects.append(dict(x=int(x), y=int(y), w=int(w), h=int(h), col=ci))
                continue
            found.append([x, y, w, h])

        # 同一个字被拆成上下两块时合成一个框（最多两格，别把相邻条目串成一条）
        found.sort(key=lambda g: g[1])
        merged = []
        for g in found:
            if merged:
                p = merged[-1]
                ox = min(p[0] + p[2], g[0] + g[2]) - max(p[0], g[0])
                nh = max(p[1] + p[3], g[1] + g[3]) - p[1]
                if (g[1] - (p[1] + p[3]) < 1.25 * pitch and ox > 0.5 * min(p[2], g[2])
                        and nh <= 2.15 * pitch):
                    p[2] = max(p[0] + p[2], g[0] + g[2]) - min(p[0], g[0])
                    p[3] = nh
                    continue
            merged.append(list(g))
        col_heads = []
        for x, y, w, h in merged:
            # 字格大小固定：小笔画（丶丿丨）也按一格给框，锚点取笔画左上
            side = min(HEAD_MAX * pitch, max(w, h, 1.02 * pitch))
            col_heads.append(dict(x=int(x), y=int(y), w=int(max(w, side)), h=int(max(h, side)),
                                  col=ci, x0=int(x0)))
        # 补：栏左缘特别小的字头（丶丿丨那种一笔的字）靠上面的判据认不出，
        # 用"缩进的属性行"反推：属性行正上方一格就是字头
        col_heads.extend(_recover_meta(ink, pitch, x0, col_right, ci, col_heads, ys, ye))
        heads.extend(col_heads)

    heads.sort(key=lambda d: (d["col"], d["y"]))
    suspects.sort(key=lambda d: (d["col"], d["y"]))
    return heads, margins, suspects


def _looks_meta(strip, left, pitch):
    """这一行像不像属性行「一部 3画 ①4E0B」：至少三块小字，而且不是居中大标题。"""
    n, _lab, st, _ = cv2.connectedComponentsWithStats(strip, connectivity=8)
    small = 0
    for i in range(1, n):
        h = int(st[i, cv2.CC_STAT_HEIGHT])
        a = int(st[i, cv2.CC_STAT_AREA])
        if a < 0.004 * pitch * pitch:
            continue
        if h > 0.90 * pitch:
            return False                     # 有大字：是「十九画」那种居中标题
        small += 1
    return small >= 3 and left >= 1.05 * pitch


def _indented_lines(ink, pitch, x0, col_right, ys, ye):
    """栏内所有"缩进的行"（属性行的候选）：返回 [(顶, 底, 最左墨迹 x)]。"""
    sub = ink[ys:ye, x0:col_right]
    row = sub.sum(axis=1) >= 4
    tol = max(3, int(0.12 * pitch))
    out = []
    i = 0
    while i < len(row):
        if not row[i]:
            i += 1
            continue
        last, j = i, i
        while j < len(row):
            if row[j]:
                last = j
                j += 1
            elif j - last > tol:
                break
            else:
                j += 1
        if 0.30 * pitch <= last - i + 1 <= 0.95 * pitch:
            cols = np.nonzero(sub[i:last + 1].sum(axis=0) > 0)[0]
            if len(cols) and _looks_meta(sub[i:last + 1], cols[0], pitch):
                out.append((ys + i, ys + last, int(cols[0])))
        i = last + 1
    return out


def _recover_meta(ink, pitch, x0, col_right, ci, col_heads, ys, ye):
    """属性行上方一格没有字头框 → 那里漏了一个小字头，按墨迹补一个固定大小的框。"""
    out = []
    bx0 = int(max(0, x0 - 0.20 * pitch))
    bx1 = int(min(ink.shape[1], x0 + 1.75 * pitch))
    for top, _bot, _left in _indented_lines(ink, pitch, x0, col_right, ys, ye):
        if any(h["y"] - 0.30 * pitch <= top <= h["y"] + h["h"] + 0.50 * pitch for h in col_heads):
            continue
        # 真正的字头行只在左上角有墨（字头 + 序号），右边大段留白；
        # 缩进的正文续行整行都有墨，靠这个区分
        if not _blank_right(ink, x0 + 3.10 * pitch, top - 1.05 * pitch, top - 0.15 * pitch, col_right):
            continue
        y1 = int(top - 0.05 * pitch)
        y0 = int(max(ys, top - 2.20 * pitch))
        if y1 - y0 < 0.4 * pitch:
            continue
        band = ink[y0:y1, bx0:bx1]
        mask = band.sum(axis=1) > 2
        rows = np.nonzero(mask)[0]
        if not len(rows) or y0 + rows[-1] < y1 - 0.45 * pitch:
            continue                             # 属性行上面是空的，不是漏切
        start = rows[-1]
        gap = 0
        for i in range(rows[-1], -1, -1):
            if mask[i]:
                start, gap = i, 0
            else:
                gap += 1
                if gap > 0.12 * pitch:
                    break
        seg = band[start:rows[-1] + 1]
        cols = np.nonzero(seg.sum(axis=0) > 0)[0]
        if not len(cols):
            continue
        by, bh = y0 + start, int(rows[-1] - start + 1)
        bx, bw = bx0 + int(cols[0]), int(cols[-1] - cols[0] + 1)
        # 只补小的：大字号字头主判据已经能认出来，这里专治「丶丿丨」这种一笔的字
        if not (0.10 * pitch <= max(bw, bh) <= 0.85 * pitch):
            continue
        side = min(HEAD_MAX * pitch, max(bw, bh, 1.02 * pitch))
        out.append(dict(x=int(bx), y=int(by), w=int(max(bw, side)), h=int(max(bh, side)),
                        col=ci, x0=int(x0)))
    return out


def _orphan_meta(ink, pitch, bounds, margins, heads, ys, ye):
    """找"有缩进的属性行、上面却没有字头框"的地方 —— 那是漏切的条目。"""
    out = []
    for ci, (bx0, bx1) in enumerate(bounds):
        if ci >= len(margins):
            break
        x0 = margins[ci]
        col_right = int(bx1 - 0.3 * pitch)
        if col_right - x0 < 2 * pitch:
            continue
        sub = ink[ys:ye, x0:col_right]
        row = sub.sum(axis=1) >= 4
        tol = max(3, int(0.12 * pitch))
        lines, i = [], 0
        while i < len(row):
            if row[i]:
                last, j = i, i
                while j < len(row):
                    if row[j]:
                        last = j
                        j += 1
                    elif j - last > tol:
                        break
                    else:
                        j += 1
                if 0.30 * pitch <= last - i + 1 <= 0.95 * pitch:
                    cols = np.nonzero(sub[i:last + 1].sum(axis=0) > 0)[0]
                    if len(cols) and _looks_meta(sub[i:last + 1], cols[0], pitch):
                        lines.append((ys + i, ys + last, int(cols[0])))
                i = last + 1
            else:
                i += 1
        hs = [h for h in heads if h["col"] == ci]
        for top, _bot, left in lines:
            if not (1.85 * pitch <= left <= 3.20 * pitch):
                continue
            if any(h["y"] - 0.25 * pitch <= top <= h["y"] + 2.15 * pitch for h in hs):
                continue
            if any(abs(s["y"] - top) < 1.4 * pitch for s in out if s["col"] == ci):
                continue
            out.append(dict(x=int(x0), y=int(max(ys, top - 1.5 * pitch)),
                            w=int(1.7 * pitch), h=int(1.8 * pitch), col=ci))
    return out


def page_boxes(ink):
    ink = drop_rules(ink)
    H = ink.shape[0]
    ink_c = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    _, _, st, _ = cv2.connectedComponentsWithStats(ink_c, connectivity=8)
    pitch = line_pitch(st, ink)
    ys, ye = int(H * 0.045), int(H * 0.975)
    bounds = column_bounds(ink, pitch)
    heads, margins, suspects = find_heads(ink, pitch, bounds, ys, ye, ink_c, st)
    return heads, pitch, margins, suspects


# ---------------------------------------------------------------- 驱动

def run_page(args):
    pdf, pno, debug = args
    try:
        ink = load_ink(pdf, pno)
    except Exception as e:                      # 空白页 / 没有嵌图
        print(f"[skip] p{pno}: {type(e).__name__}: {e}", flush=True)
        return pno, [], []
    heads, pitch, margins, suspects = page_boxes(ink)
    recs = [dict(page=pno, seq=i, col=h["col"] + 1, x=h["x"], y=h["y"], w=h["w"], h=h["h"],
                 pitch=int(round(pitch))) for i, h in enumerate(heads, 1)]
    if debug:
        vis = cv2.cvtColor((1 - ink) * 255, cv2.COLOR_GRAY2BGR)
        for i, h in enumerate(heads, 1):
            cv2.rectangle(vis, (h["x"], h["y"]), (h["x"] + h["w"], h["y"] + h["h"]), (0, 0, 235), 4)
            cv2.putText(vis, str(i), (h["x0"], h["y"] + int(1.7 * pitch)),
                        cv2.FONT_HERSHEY_SIMPLEX, pitch / 55.0, (200, 0, 0), 3)
        for s in suspects:
            cv2.rectangle(vis, (s["x"], s["y"]), (s["x"] + s["w"], s["y"] + s["h"]), (0, 140, 255), 4)
        for x0 in margins:
            cv2.line(vis, (x0, 0), (x0, ink.shape[0]), (0, 160, 0), 2)
        os.makedirs(debug, exist_ok=True)
        cv2.imwrite(os.path.join(debug, f"p{pno:04d}.png"), vis)
    return pno, recs, suspects


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default=PDF)
    ap.add_argument("--start", type=int, default=FIRST_PAGE)
    ap.add_argument("--end", type=int, default=LAST_PAGE)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="", help="输出目录（必填；原硬编码路径已移除）")
    ap.add_argument("--jobs", type=int, default=min(12, max(1, (os.cpu_count() or 2) - 4)),
                    help="并行进程数；每页要占几百 MB，别开太大")
    ap.add_argument("--debug", default="", help="另存叠框图到该目录（调试用）")
    args = ap.parse_args()

    pages = list(range(args.start, min(args.end, LAST_PAGE) + 1))
    if args.limit:
        pages = pages[:args.limit]
    os.makedirs(args.out, exist_ok=True)

    t0 = time.time()
    total = 0
    counts = {}
    idx_path = os.path.join(args.out, "index.ndjson")
    sus_path = os.path.join(args.out, "suspects.ndjson")
    jobs = [(args.pdf, p, args.debug) for p in pages]
    ex = None
    with open(idx_path, "w", encoding="utf-8", newline="\n") as fh, \
            open(sus_path, "w", encoding="utf-8", newline="\n") as sh:
        try:
            if args.jobs > 1:
                ex = ProcessPoolExecutor(max_workers=args.jobs)
                it = ex.map(run_page, jobs, chunksize=4)
            else:
                it = (run_page(j) for j in jobs)
            for pno, recs, susp in it:
                counts[pno] = len(recs)
                total += len(recs)
                for r in recs:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                for s in susp:
                    sh.write(json.dumps(dict(page=pno, **s), ensure_ascii=False) + "\n")
                if len(counts) % 20 == 0:
                    el = time.time() - t0
                    print(f"  {len(counts)}/{len(pages)} 页  框 {total}  "
                          f"{el / len(counts):.2f}s/页  "
                          f"剩余 {el / len(counts) * (len(pages) - len(counts)) / 60:.1f}min",
                          flush=True)
        finally:
            if ex is not None:
                ex.shutdown()

    with open(os.path.join(args.out, "page_counts.ndjson"), "w",
              encoding="utf-8", newline="\n") as f:
        for p in sorted(counts):
            f.write(json.dumps({"page": p, "n": counts[p]}) + "\n")
    print(f"完成 {len(counts)} 页：框 {total}，用时 {(time.time() - t0) / 60:.1f}min -> {args.out}")


if __name__ == "__main__":
    main()






