"""康熙内府本：自动定位「字头大字」，给上游没有矩形的位置补上坐标。

上游 `glyphs-original` 只给了 46,416 处字头的矩形；GlyphWiki 位置表有 48,737 处，
差的 2,321 处（多是正文里并列的「古文/异体」字形）没有矩形也没有图。

做法：
1. `big_glyphs()`：整叶找大字。墨迹二值 → 开运算取「粗笔画」→ 只保留含粗笔画的分量
   （注文小字笔画细，整行被滤掉）→ 距离合并偏旁 → 过长的块按空行拆开。
2. 位置派给框：同一叶上的位置按字表顺序排，已知矩形是锚点；两个锚点之间的空位
   取「阅读顺序上落在这两个锚点之间」的空闲框（同列看上下、跨列看左右）。
   空位多于一个时用形状分（该字上游净字图 vs 框内墨迹，归一化后算 IoU）做 DP 选。
3. 形状分低于 `MIN_SHAPE` 的直接丢弃——验证集上 <0.30 全错、>=0.40 九成以上对。

自检（把已知矩形随机遮住再重新定位）：
    python tools/kx_locate.py --val 70

产出（给 `build_kx_sprites.py` 用）：
    python tools/kx_locate.py --all          # 写 _up/kangxi/cache/kx-detected.json
"""
import argparse
import collections
import glob
import json
import os
import random
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_kx_sprites as B  # noqa: E402

UP = B.UP
CACHE = B.CACHE
OUT_JSON = os.path.join(CACHE, "kx-detected.json")

EXCLUDE_IOU = 0.35   # 与任一已有记录矩形重叠到此值 → 这个大字已经解释过了
MIN_SHAPE = 0.40     # 形状分下限
SAME_COL = 60.0      # x 中心差在此值内视为同一列
COL_TOL = 35.0       # 跨列取候选时的横向容差
DETECT = dict(thick_px=9, join_px=14, min_side=95, min_area=1500)


# ── 检测 ─────────────────────────────────────────────────────────────

def ink_mask(a):
    paper = float(np.percentile(a, 80))
    thr = max(58.0, paper * 0.76)
    return (a < thr).astype(np.uint8)


def big_glyphs(a, thick_px=DETECT["thick_px"], join_px=DETECT["join_px"],
               min_side=DETECT["min_side"], min_area=DETECT["min_area"]):
    """扫描叶 → 大字框 [(x0, y0, x1, y1)]（按 x、y 排序）。"""
    m = ink_mask(a) * 255
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = np.zeros(n, bool)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= 40:
            keep[i] = True
    m = np.where(keep[lab], 255, 0).astype(np.uint8)

    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (thick_px, thick_px))
    thick = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
    n2, lab2 = cv2.connectedComponents(m, connectivity=8)
    thick_lab = np.unique(lab2[thick > 0])
    solid = ((np.isin(lab2, thick_lab[thick_lab > 0])) & (m > 0)).astype(np.uint8) * 255

    joined = cv2.dilate(solid, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (join_px, join_px)))
    ng, labg = cv2.connectedComponents(joined, connectivity=8)
    groups = []
    for i in range(1, ng):
        ys, xs = np.where(labg == i)
        if len(ys) == 0:
            continue
        sub = solid[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        if not (sub > 0).any():
            continue
        groups.append((xs.min(), ys.min(), sub))

    hs = [g[2].shape[0] for g in groups if 100 <= g[2].shape[0] <= 210]
    med_h = float(np.median(hs)) if hs else 160.0

    boxes = []
    for gx0, gy0, sub in groups:
        gh = sub.shape[0]
        if gh > max(med_h * 1.45, sub.shape[1] * 1.35):
            # 与下方「唐韻」框/注文粘连：按空行拆
            rows = (sub > 0).sum(axis=1)
            thr = max(3.0, rows.max() * 0.10)
            run = None
            for k2, r in enumerate(rows):
                if r >= thr and run is None:
                    run = k2
                elif r < thr and run is not None:
                    boxes.append((gx0, gy0 + run, gx0 + sub.shape[1] - 1, gy0 + k2 - 1))
                    run = None
            if run is not None:
                boxes.append((gx0, gy0 + run, gx0 + sub.shape[1] - 1, gy0 + gh - 1))
        else:
            boxes.append((gx0, gy0, gx0 + sub.shape[1] - 1, gy0 + gh - 1))

    kept = []
    for x0, y0, x1, y1 in boxes:
        ys, xs = np.where(solid[y0:y1 + 1, x0:x1 + 1] > 0)
        if len(ys) == 0:
            continue
        x0, x1 = x0 + xs.min(), x0 + xs.max()
        y0, y1 = y0 + ys.min(), y0 + ys.max()
        w, h = x1 - x0 + 1, y1 - y0 + 1
        if w < min_side or h < min_side or (w * h) < min_area:
            continue
        if w > 400 or h > 400:
            continue
        kept.append((int(x0), int(y0), int(x1), int(y1)))
    kept.sort(key=lambda b: (b[0], b[1]))
    return kept


# ── 派位 ─────────────────────────────────────────────────────────────

def box_iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua else 0.0


def xc(b):
    return (b[0] + b[2]) / 2.0


def yc(b):
    return (b[1] + b[3]) / 2.0


def order(b):
    return (-xc(b), yc(b))


def masks_iou(a, b):
    if a is None or b is None:
        return 0.0
    u = (a | b).sum()
    return 0.0 if not u else float((a & b).sum()) / float(u)


SHAPE_PAD = 12      # 取框内字形时外扩的像素（防裁掉笔画；按墨迹外框归一化后不受平移影响）


def shape_of(arr, box, tmpl_norm):
    """框内字形与该字模板的重合度（各自按墨迹外框归一化到 64×64 再算 IoU）。

    两种取墨方式取较大值：
    * 按纸张亮度定阈值、外扩 4px —— 对紧贴字形的框最准；
    * Otsu、外扩 12px —— 检出的框常贴得太紧，裁掉一笔会让归一化后的形状全变。
    """
    if tmpl_norm is None:
        return 0.0
    h, w = arr.shape[:2]
    best = 0.0
    for pad, otsu in ((4, False), (SHAPE_PAD, True)):
        y0, y1 = max(0, box[1] - pad), min(h, box[3] + pad)
        x0, x1 = max(0, box[0] - pad), min(w, box[2] + pad)
        sub = arr[y0:y1, x0:x1]
        if sub.size == 0:
            continue
        if otsu:
            ink = cv2.threshold(sub, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1] > 0
        else:
            ink = ink_mask(sub)
        ratio = ink.mean()
        if ratio < 0.02 or ratio > 0.60:
            continue
        best = max(best, masks_iou(B.norm_mask(ink), tmpl_norm))
    return best


class Templates:
    """每字一张归一化模板（取上游任一净字图）。"""

    def __init__(self, records, glyph_dir):
        self.dir = glyph_dir
        self.files = {}
        for r in records:
            self.files.setdefault(r["char"], r["file"])
        self.cache = {}
        self.raw = {}

    def get(self, ch):
        if ch in self.cache:
            return self.cache[ch]
        m = None
        f = self.files.get(ch)
        if f:
            p = os.path.join(self.dir, f)
            if os.path.exists(p):
                g = B.glyph_template(p)
                if g is not None:
                    m = B.norm_mask(g)
        self.cache[ch] = m
        return m

    def gray(self, ch):
        """该字的净字灰度图（用于整叶多尺度模板搜索）。"""
        if ch in self.raw:
            return self.raw[ch]
        g = None
        f = self.files.get(ch)
        if f:
            p = os.path.join(self.dir, f)
            if os.path.exists(p):
                g = cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_GRAYSCALE)
                if g is not None:
                    ink = g < 128
                    ys, xs = np.where(ink)
                    if len(ys):
                        g = g[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        self.raw[ch] = g
        return g


def template_hit(arr, blocked, gray, scales=np.arange(0.30, 0.96, 0.05)):
    """整叶多尺度灰度模板搜索，返回 (score, box)；被 blocked 覆盖的地方当白纸。

    用于「书上印成小字」的位置（正文里的古文/异体），大字检测找不到它们。
    """
    if gray is None:
        return 0.0, None
    page = arr if blocked is None else np.where(blocked, 255, arr).astype(np.uint8)
    best = (0.0, None)
    for s in scales:
        tw, th = int(round(gray.shape[1] * s)), int(round(gray.shape[0] * s))
        if tw < 24 or th < 24 or tw >= page.shape[1] or th >= page.shape[0]:
            continue
        t = cv2.resize(gray, (tw, th), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(page, t, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx > best[0]:
            best = (float(mx), (loc[0], loc[1], loc[0] + tw - 1, loc[1] + th - 1))
    return best


FILL_SCALES = tuple(np.arange(0.38, 1.09, 0.05))   # 字号从正文小字到字头大字
FILL_MIN_TM = 0.38        # 模板相关的下限（低于此的局部极大值不看）
FILL_MIN_SHAPE = 0.40     # 形状分下限（与 MIN_SHAPE 一致）
FILL_TAKEN_IOU = 0.12     # 与已解释矩形重叠到此值 → 这个框是别的字
FILL_CLASH_IOU = 0.35     # 两个补切位置抢同一个框时，后来的让开


def multi_hits(arr, blocked, gray, scales=FILL_SCALES, topk=6, min_score=FILL_MIN_TM):
    """整叶多尺度模板搜索，返回按分数降序的 [(score, box)]（互相 IoU>0.3 的只留最高分）。

    与 `template_hit` 的区别：不只要一个全局最优。正文里并列的「古文」小字彼此都像，
    单个最优常被噪声抢走；留几个候选，让后面的形状分与「谁的模板」来挑。
    """
    if gray is None:
        return []
    page = arr if blocked is None else np.where(blocked, 255, arr).astype(np.uint8)
    found = []
    for s in scales:
        tw, th = int(round(gray.shape[1] * s)), int(round(gray.shape[0] * s))
        if tw < 24 or th < 24 or tw >= page.shape[1] or th >= page.shape[0]:
            continue
        t = cv2.resize(gray, (tw, th), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(page, t, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= min_score)
        if len(ys) == 0:
            continue
        vals = res[ys, xs]
        for k in np.argsort(-vals)[:topk]:
            x, y = int(xs[k]), int(ys[k])
            found.append((float(vals[k]), (x, y, x + tw - 1, y + th - 1)))
    found.sort(key=lambda p: -p[0])
    out = []
    for sc, b in found:
        if any(box_iou(b, c) > 0.30 for _, c in out):
            continue
        out.append((sc, b))
        if len(out) >= topk:
            break
    return out


def big_glyphs_multi(arr, sets=None):
    """几组检测参数各跑一遍再合并（细笔画的字在严参下会被整行滤掉）。"""
    if sets is None:
        sets = (DETECT,
                dict(thick_px=7, join_px=11, min_side=80, min_area=1100),
                dict(thick_px=12, join_px=18, min_side=95, min_area=1800))
    out = []
    for kw in sets:
        for b in big_glyphs(arr, **kw):
            if any(box_iou(b, c) > 0.55 for c in out):
                continue
            out.append(b)
    out.sort(key=lambda b: (b[0], b[1]))
    return out


def blocked_mask(shape, rects, pad=6):
    if not rects:
        return None
    m = np.zeros(shape[:2], bool)
    for r in rects:
        x0, y0, x1, y1 = r
        m[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad] = True
    return m


def candidates(A, Bx, free):
    """取「A 与 B 之间」的空闲大字框（按阅读顺序）。"""
    if A is not None and Bx is not None:
        if abs(xc(A) - xc(Bx)) < SAME_COL:
            lo, hi = min(yc(A), yc(Bx)), max(yc(A), yc(Bx))
            col = (xc(A) + xc(Bx)) / 2.0
            got = [b for b in free if lo < yc(b) < hi and abs(xc(b) - col) < SAME_COL + 20]
            return sorted(got, key=yc)
        lo, hi = min(xc(A), xc(Bx)), max(xc(A), xc(Bx))
        got = [b for b in free if lo - COL_TOL < xc(b) < hi + COL_TOL]
        return sorted(got, key=order)
    if A is not None:
        col = sorted([b for b in free if abs(xc(b) - xc(A)) < SAME_COL and yc(b) > yc(A)], key=yc)
        if col:
            return col
        return sorted([b for b in free if xc(b) < xc(A) - SAME_COL], key=order)
    if Bx is not None:
        col = sorted([b for b in free if abs(xc(b) - xc(Bx)) < SAME_COL and yc(b) < yc(Bx)],
                     key=yc, reverse=True)
        if col:
            return col
        return sorted([b for b in free if xc(b) > xc(Bx) + SAME_COL], key=order, reverse=True)
    return []


def solve_gap(cands, chars, tmpl, arr):
    """把若干位置派给候选框：顺序单调 + 形状分最大。"""
    n = len(chars)
    if not cands:
        return [None] * n
    if len(cands) == n:
        return list(cands)
    if len(cands) < n:
        return list(cands) + [None] * (n - len(cands))
    sc = [[shape_of(arr, b, tmpl.get(ch)) for b in cands] for ch in chars]
    m = len(cands)
    NEG = -9.0
    dp = [[NEG] * (m + 1) for _ in range(n + 1)]
    bk = [[0] * (m + 1) for _ in range(n + 1)]
    for j in range(m + 1):
        dp[0][j] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best, arg = dp[i][j - 1], 0
            if dp[i - 1][j - 1] > NEG:
                v = dp[i - 1][j - 1] + sc[i - 1][j - 1]
                if v > best:
                    best, arg = v, 1
            dp[i][j], bk[i][j] = best, arg
    out, i, j = [None] * n, n, m
    while i > 0 and j > 0:
        if bk[i][j]:
            out[i - 1] = cands[j - 1]
            i -= 1
        j -= 1
    return out


def locate_leaf(jobs, rects, arr, tmpl, drop=frozenset(), hits=None):
    """jobs：该叶上的位置（字表顺序），含 prev 记录；drop：当作未知看的 (page, index)。

    候选框 = 检出的大字框 ∪ 每个「小字位置」用它自己的字形模板做整叶搜索得到的最优框；
    再用阅读顺序 + 形状分做 DP 派位。返回 {job 序号: (x0, y0, x1, y1)}。
    """
    hidden = {(j["rec"]["x0"], j["rec"]["y0"], j["rec"]["x1"], j["rec"]["y1"])
              for j in jobs
              if j["rec"] is not None
              and (j["entry"]["page"], j["entry"]["index"]) in drop}
    explain = [r for r in rects if r["rect"] not in hidden]
    boxes = big_glyphs(arr)
    free = [b for b in boxes
            if not any(box_iou(b, r["rect"]) >= EXCLUDE_IOU for r in explain)]
    known = {}
    for i, j in enumerate(jobs):
        rec = j["rec"]
        if rec is None or (j["entry"]["page"], j["entry"]["index"]) in drop:
            continue
        known[i] = (rec["x0"], rec["y0"], rec["x1"], rec["y1"])

    # 小字位置：自己找一次（大字检测抓不到它们）
    blocked = blocked_mask(arr.shape, [r["rect"] for r in explain])
    extra = {}
    for i, j in enumerate(jobs):
        if i in known:
            continue
        if (j["entry"]["page"], j["entry"]["index"]) in drop and j["rec"] is not None:
            continue
        ch = j["entry"]["char"]
        if hits is not None and ch in hits:
            extra[i] = hits[ch]
            continue
        got = template_hit(arr, blocked, tmpl.gray(ch))
        if hits is not None:
            hits[ch] = got[1]
        if got[1] is not None:
            extra[i] = got[1]

    out, i, n = {}, 0, len(jobs)
    while i < n:
        if i in known:
            i += 1
            continue
        j = i
        while j < n and j not in known:
            j += 1
        gap = list(range(i, j))
        A = known.get(i - 1)
        Bx = known.get(j) if j < n else None
        cand = candidates(A, Bx, free)
        # 自己找到的框并进来（去重），再按阅读顺序排
        for k in gap:
            b = extra.get(k)
            if b is None:
                continue
            if any(box_iou(b, c) > 0.5 for c in cand):
                continue
            cand = cand + [b]
        cand.sort(key=order)
        picked = solve_gap(cand, [jobs[k]["entry"]["char"] for k in gap], tmpl, arr)
        for k, b in zip(gap, picked):
            if b is None:
                continue
            if shape_of(arr, b, tmpl.get(jobs[k]["entry"]["char"])) < MIN_SHAPE:
                continue    # 形状对不上：宁可空着，也不塞一个别的字
            out[k] = b
            free = [f for f in free if f != b]
        i = j
    return out


def fill_leaf(jobs, rects, arr, tmpl, targets, hits=None):
    """补切：给 `targets`（jobs 里的序号）逐个用自己的字形模板整叶找框。

    与 `locate_leaf` 不同，这里不按阅读顺序派位，而是「谁的模板最像谁就归谁」：
    正文里并列的「古文」小字常常是同一行连着好几个位置，按顺序派位容易错开一格。
    `hits` 是跨叶共享的模板搜索结果缓存（一个字只搜一次）。
    """
    explained = [r["rect"] for r in rects]
    blocked = blocked_mask(arr.shape, explained)
    cand = {}
    for n in targets:
        ch = jobs[n]["entry"]["char"]
        norm = tmpl.get(ch)
        if hits is not None and ch in hits:
            got_hits = hits[ch]
        else:
            got_hits = multi_hits(arr, blocked, tmpl.gray(ch))
            if hits is not None:
                hits[ch] = got_hits
        got = []
        for sc, b in got_hits:
            if any(box_iou(b, r) > FILL_TAKEN_IOU for r in explained):
                continue
            sh = shape_of(arr, b, norm)
            val = max(sh, sc * 0.85) if norm is not None else sc * 0.85
            if val >= FILL_MIN_SHAPE and (norm is not None or sc >= 0.55):
                got.append((val, b))
        got.sort(key=lambda p: -p[0])
        cand[n] = got

    order = sorted(targets, key=lambda n: -(cand[n][0][0] if cand[n] else -1.0))
    taken, out = [], {}
    for n in order:
        for val, b in cand[n]:
            if any(box_iou(b, t) > FILL_CLASH_IOU for t in taken):
                continue
            out[n] = b
            taken.append(b)
            break
    return out


# ── 数据 ─────────────────────────────────────────────────────────────

def all_rects():
    """全部记录（含部首标题）的矩形：①推列网格 ②排除「已经解释过」的大字。"""
    raw = json.load(open(B.DEFAULT_MANIFEST, encoding="utf-8"))
    out = []
    for r in raw:
        try:
            x0, y0 = int(r["\u9875\u9762x0"]), int(r["\u9875\u9762y0"])
            x1, y1 = int(r["\u9875\u9762x1"]), int(r["\u9875\u9762y1"])
        except (KeyError, TypeError, ValueError):
            continue
        if x1 - x0 < 40 or y1 - y0 < 40:
            continue
        out.append({"cat": r[B.F_CAT], "char": r[B.F_CHAR],
                    "vol": str(r[B.F_VOL]).zfill(2), "pdf": int(r[B.F_PAGE]),
                    "rect": (x0, y0, x1, y1)})
    return out


def load_jobs():
    entries = B.parse_group(B.DEFAULT_GROUP)
    records = B.load_records(B.DEFAULT_MANIFEST)
    matched, _, _ = B.match_entries(entries, records)
    jobs, prev = [], None
    for e in entries:
        rec = matched.get((e["page"], e["index"]))
        leaf = (rec["vol"], rec["pdf"]) if rec else ((prev["vol"], prev["pdf"]) if prev else None)
        jobs.append({"entry": e, "rec": rec, "leaf": leaf})
        if rec is not None:
            prev = rec
    return entries, records, jobs


def merge_detected():
    """合并各册的定位结果与第二轮补切结果 → cache/kx-detected.json。"""
    out = {}
    parts = sorted(glob.glob(os.path.join(CACHE, "kx-det-*.json")))
    parts.append(os.path.join(CACHE, "kx-filled.json"))
    for p in parts:
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding="utf-8"))
        out.update(d)
        print("  %-18s %5d" % (os.path.basename(p), len(d)))
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    sh = sorted(v["shape"] for v in out.values())
    print("合计 %d 处，形状分 中位 %.2f / 10%% 位 %.2f / 最低 %.2f"
          % (len(out), sh[len(sh) // 2], sh[len(sh) // 10], sh[0]))
    print("写出", OUT_JSON)


def fill_targets(det, jobs, vol=None):
    """还没定位的位置（按叶分组）。"""
    want = collections.defaultdict(list)
    for i, j in enumerate(jobs):
        e = j["entry"]
        if j["rec"] is not None or not j["leaf"] or B.e_key(e) in det:
            continue
        if vol and j["leaf"][0] != vol:
            continue
        want[j["leaf"]].append(i)
    return want


def fill_leaves(want, jobs, rects_by_leaf, tmpl):
    """逐叶补切，返回 {key: 记录}。want: {叶: [jobs 序号]}。"""
    found = {}
    hits = {}
    pmc, volc = {}, {}

    def page_of(vol, pdf):
        if vol not in pmc:
            pmc[vol] = B.load_pagemap(vol) or {}
        idx = pmc[vol].get(pdf)
        if idx is None:
            return None
        if vol not in volc:
            volc[vol] = B.Volume(vol)
        return volc[vol].array(idx)

    for n, lf in enumerate(sorted(want), 1):
        arr = page_of(*lf)
        if arr is None:
            continue
        targets = want[lf]
        lf_jobs = [jobs[i] for i in targets]
        got = fill_leaf(lf_jobs, rects_by_leaf[lf], arr, tmpl, list(range(len(targets))), hits)
        for k, b in got.items():
            e = lf_jobs[k]["entry"]
            found[B.e_key(e)] = {
                "page": e["page"], "index": e["index"], "char": e["char"],
                "box": list(b),
                "shape": round(shape_of(arr, b, tmpl.get(e["char"])), 3),
            }
        if n % 25 == 0:
            print("  叶 %d/%d，已补 %d" % (n, len(want), len(found)), flush=True)
    for v in volc.values():
        v.close()
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--val", type=int, default=0, help="自检：随机遮住 15%% 已知位置，跑 N 叶")
    ap.add_argument("--vol", default=None, help="只跑某一册（01-13）")
    ap.add_argument("--all", action="store_true", help="跑全部，写 cache/kx-detected.json")
    ap.add_argument("--fill", action="store_true",
                    help="第二轮补切：给 kx-detected.json 之外的位置单独找框，写 cache/kx-filled.json")
    ap.add_argument("--fillval", type=int, default=0,
                    help="补切自检：随机遮住 N 叶里 15%% 已知位置，看补切能找回多少、错多少")
    ap.add_argument("--merge", action="store_true",
                    help="把 cache/kx-det-*.json 与 kx-filled.json 合并成 kx-detected.json")
    ap.add_argument("--threads", type=int, default=0, help="cv2 线程数（0=默认）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.threads:
        import cv2
        cv2.setNumThreads(args.threads)

    if args.merge:
        merge_detected()
        return

    entries, records, jobs = load_jobs()
    rects_by_leaf = collections.defaultdict(list)
    for r in all_rects():
        rects_by_leaf[(r["vol"], r["pdf"])].append(r)
    tmpl = Templates(records, B.DEFAULT_GLYPHS)

    leaf_of = collections.defaultdict(list)
    for i, j in enumerate(jobs):
        if j["leaf"]:
            leaf_of[j["leaf"]].append(i)

    if args.val:
        leaves = sorted(leaf_of)
        rnd = random.Random(20260314)
        rnd.shuffle(leaves)
        leaves = leaves[:args.val]
        drops, buckets = {}, []
        for lf in leaves:
            drops[lf] = set()
        tot = ok = 0
        for lf in leaves:
            rnd2 = random.Random(hash(lf) & 0xFFFF)
            drop = set()
            for i in leaf_of[lf]:
                j = jobs[i]
                if j["rec"] is not None and rnd2.random() < 0.15:
                    drop.add((j["entry"]["page"], j["entry"]["index"]))
            if not drop:
                continue
            arr = None
            pm = B.load_pagemap(lf[0]) or {}
            if lf[1] in pm:
                v = B.Volume(lf[0])
                arr = v.array(pm[lf[1]])
                v.close()
            if arr is None:
                continue
            lf_jobs = [jobs[i] for i in leaf_of[lf]]
            got = locate_leaf(lf_jobs, rects_by_leaf[lf], arr, tmpl, drop)
            for k, b in got.items():
                j = lf_jobs[k]
                if (j["entry"]["page"], j["entry"]["index"]) not in drop:
                    continue
                rect = (j["rec"]["x0"], j["rec"]["y0"], j["rec"]["x1"], j["rec"]["y1"])
                v = box_iou(b, rect)
                tot += 1
                ok += 1 if v >= 0.5 else 0
                buckets.append((shape_of(arr, b, tmpl.get(j["entry"]["char"])), v))
        print("遮住 %d 处，重新定位正确 %d (%.1f%%)" % (tot, ok, 100.0 * ok / max(1, tot)))
        for lo, hi in ((0.0, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.65), (0.65, 1.01)):
            grp = [v for s, v in buckets if lo <= s < hi]
            if grp:
                print("  形状分 %.2f-%.2f：%3d 例，正确 %3d (%.0f%%)"
                      % (lo, hi, len(grp), sum(1 for v in grp if v >= 0.5),
                         100.0 * sum(1 for v in grp if v >= 0.5) / len(grp)))
        return

    leaves = [lf for lf in sorted(leaf_of) if any(jobs[i]["rec"] is None for i in leaf_of[lf])]
    if args.vol:
        leaves = [lf for lf in leaves if lf[0] == args.vol]

    if args.fill:
        det = {}
        det_path = OUT_JSON if os.path.exists(OUT_JSON) else None
        if det_path:
            det = json.load(open(det_path, encoding="utf-8"))
        for p in sorted(glob.glob(os.path.join(CACHE, "kx-det-*.json"))):
            det.update(json.load(open(p, encoding="utf-8")))
        want = fill_targets(det, jobs, args.vol)
        print("补切目标 %d 处，涉及 %d 叶" % (sum(len(v) for v in want.values()), len(want)),
              flush=True)
        found = fill_leaves(want, jobs, rects_by_leaf, tmpl)
        out = args.out or os.path.join(CACHE, "kx-filled%s.json" % ("-" + args.vol if args.vol else ""))
        json.dump(found, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        sh = sorted(v["shape"] for v in found.values())
        print("写出 %s 共 %d 处（形状分中位 %.2f）"
              % (out, len(found), sh[len(sh) // 2] if sh else 0))
        return

    if args.fillval:
        rnd = random.Random(20260601)
        pool = [lf for lf in leaves if any(jobs[i]["rec"] is not None for i in leaf_of[lf])]
        rnd.shuffle(pool)
        pool = pool[:args.fillval]
        tot = ok = 0
        dropped = 0
        buckets = []
        for lf in pool:
            rnd2 = random.Random(hash(lf) & 0xFFFF)
            drop = {i for i in leaf_of[lf]
                    if jobs[i]["rec"] is not None and rnd2.random() < 0.15}
            if not drop:
                continue
            dropped += len(drop)
            arr = None
            pm = B.load_pagemap(lf[0]) or {}
            if lf[1] in pm:
                v = B.Volume(lf[0])
                arr = v.array(pm[lf[1]])
                v.close()
            if arr is None:
                continue
            rects = [r for r in rects_by_leaf[lf]
                     if r["rect"] not in {(jobs[i]["rec"]["x0"], jobs[i]["rec"]["y0"],
                                           jobs[i]["rec"]["x1"], jobs[i]["rec"]["y1"])
                                          for i in drop if jobs[i]["rec"] is not None}]
            lf_jobs = [jobs[i] for i in sorted(drop)]
            got = fill_leaf(lf_jobs, rects, arr, tmpl, list(range(len(lf_jobs))))
            for k, b in got.items():
                j = lf_jobs[k]
                rect = (j["rec"]["x0"], j["rec"]["y0"], j["rec"]["x1"], j["rec"]["y1"])
                iou = box_iou(b, rect)
                tot += 1
                ok += 1 if iou >= 0.5 else 0
                buckets.append((shape_of(arr, b, tmpl.get(j["entry"]["char"])), iou))
        print("遮住 %d 处，找回 %d，其中正确 %d (%.1f%%；占遮住的 %.0f%%)"
              % (dropped, tot, ok, 100.0 * ok / max(1, tot), 100.0 * ok / max(1, dropped)))
        for lo, hi in ((0.0, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.65), (0.65, 1.01)):
            grp = [v for s, v in buckets if lo <= s < hi]
            if grp:
                print("  形状分 %.2f-%.2f：%3d 例，正确 %3d (%.0f%%)"
                      % (lo, hi, len(grp), sum(1 for v in grp if v >= 0.5),
                         100.0 * sum(1 for v in grp if v >= 0.5) / len(grp)))
        return
    print("待补叶数", len(leaves), flush=True)
    found = {}
    pmc, volc = {}, {}

    def page_of(vol, pdf):
        if vol not in pmc:
            pmc[vol] = B.load_pagemap(vol) or {}
        idx = pmc[vol].get(pdf)
        if idx is None:
            return None
        if vol not in volc:
            volc[vol] = B.Volume(vol)
        return volc[vol].array(idx)

    # 叶的阅读顺序（下一页 = 紧跟其后的那一叶），用于处理「位置跨到下一叶」的情况
    seq = sorted(leaf_of, key=lambda lf: min(leaf_of[lf]))
    nxt = {seq[i]: seq[i + 1] for i in range(len(seq) - 1)}

    def record(lf_jobs, got, arr, into, only=None):
        for k, b in got.items():
            j = lf_jobs[k]
            if j["rec"] is not None:
                continue
            if only is not None and k >= only:
                continue
            e = j["entry"]
            into[B.kx_key(e["page"], e["index"])] = {
                "page": e["page"], "index": e["index"], "char": e["char"],
                "box": list(b), "shape": round(shape_of(arr, b, tmpl.get(e["char"])), 3),
            }

    for n, lf in enumerate(leaves, 1):
        arr = page_of(*lf)
        if arr is None:
            continue
        lf_jobs = [jobs[i] for i in leaf_of[lf]]
        got = locate_leaf(lf_jobs, rects_by_leaf[lf], arr, tmpl)
        record(lf_jobs, got, arr, found)
        if n % 25 == 0:
            print("  叶 %d/%d，已定位 %d" % (n, len(leaves), len(found)), flush=True)

    # 位置跨到下一叶的：上一叶排满后，剩下的位置其实印在下一页开头
    by_next = collections.defaultdict(list)
    for i, j in enumerate(jobs):
        if j["rec"] is not None or not j["leaf"]:
            continue
        if args.vol and j["leaf"][0] != args.vol:
            continue
        if B.kx_key(j["entry"]["page"], j["entry"]["index"]) in found:
            continue
        nl = nxt.get(j["leaf"])
        if nl and leaf_of.get(nl):
            by_next[nl].append(i)
    print("跨叶重试 %d 处，涉及 %d 叶" % (sum(len(v) for v in by_next.values()), len(by_next)),
          flush=True)
    for nl, js in sorted(by_next.items()):
        arr2 = page_of(*nl)
        if arr2 is None:
            continue
        front = [jobs[j] for j in leaf_of[nl] if jobs[j]["rec"] is not None]
        if not front:
            continue
        got2 = locate_leaf([jobs[i] for i in js] + front, rects_by_leaf[nl], arr2, tmpl)
        record([jobs[i] for i in js] + front, got2, arr2, found, only=len(js))
    for v in volc.values():
        v.close()
    out = args.out or (OUT_JSON if not args.vol else os.path.join(CACHE, "kx-det-%s.json" % args.vol))
    json.dump(found, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("写出", out, "共", len(found), "处")


if __name__ == "__main__":
    main()
