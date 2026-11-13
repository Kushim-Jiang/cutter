"""《中华字海》扫描页 -> 字头大字图。

按正文版面网格定位（不靠"大字"尺寸去猜）：
  1. 版心三栏：竖直投影找两条栏缝 -> 3 个栏。
  2. 每栏的行距 pitch 与相位：行投影自相关 + 梳状拟合，得到等间距行网格。
  3. 字格宽 cw ≈ 正文字高 bh（方块字）。
  4. 字头窗口 = 行 i 顶起 2*pitch 高 × 栏左边起 3*cw 宽；
     窗口里若有高度 >= 1.4*pitch 的连通域，就是字头；命中后跳过下一行。
"""
import numpy as np
import cv2


def load_ink(doc, pno):
    """取页面内嵌的原始 1bit 扫描图（不重采样），返回 ink 掩码。

    1bit PNG 解出来是 0=白底、255=墨，与常见约定相反。
    """
    page = doc[pno - 1]
    info = doc.extract_image(page.get_images(full=True)[0][0])
    arr = cv2.imdecode(np.frombuffer(info["image"], np.uint8), cv2.IMREAD_GRAYSCALE)
    ink = (arr > 0).astype(np.uint8)
    if ink.mean() > 0.5:
        ink = 1 - ink
    return ink


def drop_rules(ink):
    H, W = ink.shape
    ink = ink.copy()
    ink[ink.sum(axis=1) > W * 0.45, :] = 0
    ink[:, ink.sum(axis=0) > H * 0.45] = 0
    return ink


def strip_streaks(ink, bh):
    """去掉扫描留下的长直线（栏线、脏边），它们会干扰左右边距的估计。"""
    v = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(3, int(3 * bh)))))
    h = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(8 * bh)), 1)))
    out = ink.copy()
    out[v > 0] = 0
    out[h > 0] = 0
    return out


# ---------------------------------------------------------------- 度量

def body_char_height(ink):
    H, W = ink.shape
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    h = stats[1:, cv2.CC_STAT_HEIGHT]
    a = stats[1:, cv2.CC_STAT_AREA]
    m = (a >= max(60, 1e-5 * H * W)) & (h >= 8) & (h < H * 0.2)
    h = h[m]
    if len(h) < 30:
        return H * 0.02
    hist = np.bincount(h, minlength=600)[:600]
    sm = np.convolve(hist, np.ones(7), mode="same")
    p = int(np.argmax(sm))
    lo = p
    while lo > 0 and sm[lo] > sm[p] * 0.5:
        lo -= 1
    hi = p
    while hi < 599 and sm[hi] > sm[p] * 0.5:
        hi += 1
    return (lo + hi) / 2.0


# ---------------------------------------------------------------- 栏

def find_bands(ink, bh):
    """竖直投影找两条栏缝。传入的 ink 应当是「只有正文字大小的连通域」的掩码，
    否则字头大字会把栏缝填掉（字头常常悬出到栏缝里）。"""
    H, W = ink.shape
    y0, y1 = int(H * 0.055), int(H * 0.975)
    prof = ink[y0:y1, :].sum(axis=0).astype(float)
    prof = np.convolve(prof, np.ones(max(3, int(bh / 3))), mode="same")
    lo, hi = int(W * 0.04), int(W * 0.98)
    thr = prof[lo:hi].max() * 0.05
    runs, s = [], None
    for x in range(lo, hi):
        if prof[x] <= thr:
            if s is None:
                s = x
        elif s is not None:
            runs.append((s, x - 1))
            s = None
    if s is not None:
        runs.append((s, hi - 1))
    # 栏里夹着竖直细线时缝会被切成两段，先合并靠得很近的
    merged = []
    for a, b in runs:
        if merged and a - merged[-1][1] <= 0.6 * bh:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    cand = [r for r in merged if r[1] - r[0] + 1 >= 0.8 * bh
            and r[0] > W * 0.12 and r[1] < W * 0.92]
    # 挑两条缝：让三栏宽度最均衡（而不是简单取最宽的两条）
    best, bestv = None, -1.0
    for i in range(len(cand)):
        for j in range(i + 1, len(cand)):
            b = [(lo, cand[i][0]), (cand[i][1] + 1, cand[j][0]), (cand[j][1] + 1, hi)]
            if min(y - x for x, y in b) < 8 * bh:
                continue
            v = min(y - x for x, y in b)
            if v > bestv:
                best, bestv = b, v
    if best:
        return best
    third = (hi - lo) / 3
    return [(int(lo + i * third), int(lo + (i + 1) * third)) for i in range(3)]


# ---------------------------------------------------------------- 行网格

def line_grid(ink, x0, x1, bh, pitch=None):
    """返回 (pitch, phase, y_body_start, y_body_end)。给了 pitch 就只拟合相位。"""
    H = ink.shape[0]
    ys, ye = int(H * 0.05), int(H * 0.975)
    prof = ink[ys:ye, x0:x1].sum(axis=1).astype(float)
    if prof.sum() <= 0:
        return None
    if pitch is None:
        p0 = prof - prof.mean()
        ac = np.correlate(p0, p0, mode="full")[len(p0) - 1:]
        lo, hi = max(3, int(bh * 1.2)), int(bh * 1.75)
        if hi <= lo + 2:
            return None
        lag = lo + int(np.argmax(ac[lo:hi]))
        pitch = float(lag)
    pitch = float(pitch)

    best_ph, best = 0, -1.0
    for ph in range(max(1, int(round(pitch)))):
        idx = np.arange(ph, len(prof), pitch).astype(int)
        s = prof[idx].sum()
        if s > best:
            best, best_ph = s, ph
    return pitch, ys + best_ph, ys, ye


def col_margin(ink, x0, x1, pitch, phase, ye):
    """栏的正文左边距：各行最左侧墨迹 x 的众数。"""
    lefts = []
    y = phase
    while y < ye:
        a = int(y)
        b = min(ink.shape[0], int(y + pitch))
        row = ink[a:b, x0:x1]
        cnt = row.sum(axis=0)
        cnt = np.convolve(cnt, np.ones(5), mode="same")
        cols = np.where(cnt >= 3)[0]
        if len(cols):
            lefts.append(int(cols[0]) + x0)
        y += pitch
    if not lefts:
        return x0
    lefts = np.array(lefts)
    hist = np.bincount(lefts - x0, minlength=x1 - x0)
    sm = np.convolve(hist, np.ones(15), mode="same")
    return int(np.argmax(sm)) + x0


# ---------------------------------------------------------------- 主体

def _union(a, b):
    x0 = min(a[0], b[0])
    y0 = min(a[1], b[1])
    x1 = max(a[0] + a[2], b[0] + b[2])
    y1 = max(a[1] + a[3], b[1] + b[3])
    return [x0, y0, x1 - x0, y1 - y0]


def margin_heads(ink, x0, cw, pitch, ys, ye):
    """在栏左缘的竖条里找"连续占满 1.5 行距以上"的墨迹带，当作字头。

    扫描件会把笔画打散成几十个小块，按连通域聚类时若没有任何一块够大就聚不起来
    （p8/p10/p11 那种淡笔画就是这么漏的）。换个信号：字头在栏左缘一定是通高的，
    正文小字则每行只有 0.7 行距高，行间有明显的白缝。
    """
    X0 = int(max(0, x0 - 1.3 * cw))
    X1 = int(min(ink.shape[1], x0 + 2.4 * cw))
    if X1 - X0 < 10:
        return []
    y0, y1 = int(max(0, ys)), int(min(ink.shape[0], ye))
    strip = ink[y0:y1, X0:X1]
    row = (strip.sum(axis=1) >= 2).astype(np.int8)
    gtol = max(3, int(0.18 * pitch))                  # 允许打断的小缝（正文行间白缝约 0.3 行距）
    runs, i = [], 0
    while i < len(row):
        if row[i]:
            last = i
            j = i
            while j < len(row):
                if row[j]:
                    last = j
                    j += 1
                elif j - last > gtol:
                    break
                else:
                    j += 1
            runs.append((i, last))
            i = last + 1
        else:
            i += 1
    out = []
    for a, b in runs:
        n = b - a + 1
        if n < 1.25 * pitch or n > 3.4 * pitch:        # 太短是正文，太长是栏线
            continue
        top = y0 + a
        bot = min(ye, top + int(1.95 * pitch))
        sub = ink[top:bot, X0:X1]
        rows_ = np.nonzero(sub.sum(axis=1) > 0)[0]
        cols = np.nonzero(sub.sum(axis=0) > 0)[0]
        if not len(rows_) or not len(cols):
            continue
        by, bh = top + int(rows_[0]), int(rows_[-1] - rows_[0] + 1)
        bx, bw = X0 + int(cols[0]), int(cols[-1] - cols[0] + 1)
        if bh >= 1.05 * pitch and bw >= 0.5 * cw:
            out.append([bx, by, bw, bh])
    return out


def _stacked_lines(sub, bh, pitch):
    """框里纵向是不是"两条都够高的墨带"——上下两行正文叠成一块时就是这样。

    真字头要么一路连到底，要么上面只有一两个小笔画（点、短横）。

    行墨量必须够（≥4% 行宽）才算"有墨"：两行正文之间常有一条 1~2px 的杂墨（上一行的
    逗号、下一行的顿点），按"有没有墨"切会把两条墨带桥接成一条，判据就永远不成立。
    """
    if sub.size == 0:
        return False
    row = (sub.sum(axis=1) >= max(3, int(0.04 * sub.shape[1]))).astype(int)
    gtol = max(3, int(0.10 * pitch))
    runs, s, gap = [], None, 0
    for i, v in enumerate(row):
        if v:
            if s is None:
                s = i
            gap = 0
        elif s is not None:
            gap += 1
            if gap > gtol:
                runs.append((s, i - gap))
                s = None
                gap = 0
    if s is not None:
        runs.append((s, len(row) - 1))
    tall = [r for r in runs if r[1] - r[0] + 1 >= 0.70 * bh]
    if len(tall) < 2:
        return False
    # 有两条高墨带，还要看每条里是不是"几个并排的小字"：上下两行正文的每条墨带都是
    # 2~3 个分开的小字，没有哪个连通块能占满带宽；而「吕」「昌」「三」这类上下分段的
    # 真字头，每段都是一整块，占满带宽。只按"两条高墨带"判会把它们误杀。
    for a, b in tall:
        band = sub[a:b + 1]
        if band.size == 0:
            return True
        n, _, st, _ = cv2.connectedComponentsWithStats(
            cv2.dilate(band, np.ones((3, 3), np.uint8)), connectivity=8)
        if not any(st[i][2] >= 0.75 * band.shape[1] for i in range(1, n)):
            return True
    return False


def _merge_split_heads(heads, cw, pitch):
    """把同一个字被拆成左右两块、并排贴着的情况合成一个。

    扫描件里笔画断开时，如果两块都够"大"，并集又超过单簇的宽度上限，就会各成一个簇。
    字头之后紧跟释文时，中间一定有明显白缝（≥0.35 字格宽），所以用 0.12 字格宽做判据很安全。
    """
    out = []
    for bi in sorted({h["band"] for h in heads}):
        band = sorted([h for h in heads if h["band"] == bi], key=lambda h: (h["y"], h["x"]))
        merged = True
        while merged:
            merged = False
            for i in range(len(band) - 1):
                a, b = band[i], band[i + 1]
                gx = max(a["x"], b["x"]) - min(a["x"] + a["w"], b["x"] + b["w"])
                oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
                if gx > 0.12 * cw or oy < 0.55 * min(a["h"], b["h"]):
                    continue
                x = min(a["x"], b["x"])
                y = min(a["y"], b["y"])
                box = dict(a, x=x, y=y,
                           w=max(a["x"] + a["w"], b["x"] + b["w"]) - x,
                           h=max(a["y"] + a["h"], b["y"] + b["h"]) - y,
                           line=min(a["line"], b["line"]))
                band[i:i + 2] = [box]
                merged = True
                break
        out.extend(band)
    return out


def _merge_vertical_parts(heads, cw, pitch):
    """把同一个字被拆成上下两块的情况合起来，并截到"上一块顶端 + 两行"。

    依据是实测的版面规律：真字头之间至少隔 2 行，相邻字头顶端间距 99% 都 ≥ 1.8 行距
    （p1 = 1.81）。所以顶端间距 < 1.5 行距的两块，几乎必然是同一个字的两截。
    合成后用"上一块顶端 + 2 行距"当底边——多出来的那截一定是下一行的释文。
    """
    out = []
    for bi in sorted({h["band"] for h in heads}):
        band = sorted([h for h in heads if h["band"] == bi], key=lambda h: h["y"])
        merged = True
        while merged:
            merged = False
            for i in range(len(band) - 1):
                a, b = band[i], band[i + 1]
                if (b["y"] - a["y"]) / pitch >= 1.5:
                    continue
                ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
                if ox < 0.5 * min(a["w"], b["w"]):
                    continue
                bottom = min(b["y"] + b["h"], a["y"] + 2.05 * pitch)
                box = dict(a, x=min(a["x"], b["x"]),
                           w=max(a["x"] + a["w"], b["x"] + b["w"]) - min(a["x"], b["x"]),
                           h=int(round(bottom - a["y"])))
                band[i:i + 2] = [box]
                merged = True
                break
        out.extend(band)
    return out


def grow_to_blob(ink, box, gap=3, slack=0.15):
    """把字框扩到包住"框内墨迹所在的那个连通块"，但只接受紧贴字框的那一小块。

    1a 的淡笔画是断点状的，判定连通前要先膨胀 gap 像素把断口接上；可是字头顶端
    往往只和上下正文隔几个像素，一膨胀就跟整行正文连成一片，直接取连通块会把
    正文一起吞进框里。所以这里划一条界：只在「字框外扩 slack*max(w,h)」的范围
    内搜，凡是被搜索窗裁断（说明它比邻域还大）的连通块一律丢弃，保持原框。
    """
    H, W = ink.shape
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    pad = max(8, int(round(slack * max(w, h))))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
    sub = ink[y0:y1, x0:x1]
    if sub.size == 0:
        return box
    if gap > 0:
        k = 2 * int(gap) + 1
        sub = cv2.dilate(sub, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(sub, connectivity=8)
    if n < 2:
        return box
    inside = lab[max(0, y - y0):max(1, y + h - y0), max(0, x - x0):max(1, x + w - x0)]
    ids = np.unique(inside)
    ids = ids[ids > 0]
    if len(ids) == 0:
        return box
    hh, ww = sub.shape
    nx0, ny0, nx1, ny1 = x, y, x + w, y + h
    got = False
    for i in ids:
        cx, cy, cw, ch = stats[i][0], stats[i][1], stats[i][2], stats[i][3]
        cut = ((x0 > 0 and cx <= 0) or (y0 > 0 and cy <= 0) or
               (x1 < W and cx + cw >= ww) or (y1 < H and cy + ch >= hh))
        if cut:
            continue
        nx0 = min(nx0, x0 + int(cx)); ny0 = min(ny0, y0 + int(cy))
        nx1 = max(nx1, x0 + int(cx) + int(cw)); ny1 = max(ny1, y0 + int(cy) + int(ch))
        got = True
    if not got:
        return box
    return dict(box, x=int(nx0), y=int(ny0), w=int(nx1 - nx0), h=int(ny1 - ny0))


def grow_heads(ink, heads, gap=3, limit=2.0):
    """对一批字头统一做连通扩张。

    扩张到"整个连通块"偶尔会把挨得很近的正文一起吞进来（面积暴涨几倍），
    所以设面积上限：超过 limit 倍就先退回不膨胀，再不行就保持原框。
    """
    out = []
    for h in heads:
        a0 = max(1, h["w"] * h["h"])
        g = grow_to_blob(ink, h, gap)
        if g["w"] * g["h"] > limit * a0:
            g0 = grow_to_blob(ink, h, 0)
            g = g0 if g0["w"] * g0["h"] <= limit * a0 else dict(h)
        out.append(g)
    return out


def column_margins(comps, pitch, cw, W):
    """栏的左边距：字头大字只出现在每栏左缘，把它们按左边缘聚成几组就是栏的位置。

    比投竖直投影找栏缝稳得多 —— 这本字典的字头会悬出到栏缝里，有时把缝整个填掉，
    栏缝宽度只有几十像素，投影法很容易退化成"三等分"。
    """
    cores = sorted(c[0] for c in comps
                   if 1.25 * pitch <= c[3] <= 2.9 * pitch and c[2] <= 3.2 * cw)
    if not cores:
        return []
    groups, cur = [], [cores[0]]
    for x in cores[1:]:
        if x - cur[-1] > 2.0 * cw:
            groups.append(cur)
            cur = [x]
        else:
            cur.append(x)
    groups.append(cur)
    out = sorted(float(np.median(g)) for g in groups if len(g) >= 3)
    merged = []
    for m in out:
        # 栏内正文碎片也会凑出一个"core 簇"，它往往偏右；两者平均出来的中点不是任何一栏的
        # 真实左缘（p88 就被平均成 248，害得整栏字头因 x < x0-1.8*cw 被判 left 全部丢掉）。
        # 栏左缘只可能取更靠左的那个。
        # 阈值放到 5 个字宽：真实相邻栏间距约 600px（≈18 字宽），所以 <5 字宽的一定是同一栏
        # 被拆出来的伪簇（p80 就因此多出一栏 1304-1521，把第三栏切碎）。
        if merged and m - merged[-1] < 5.0 * cw:
            merged[-1] = min(merged[-1], m)
        else:
            merged.append(m)
    return merged


def _group_runs(cnt, thr, gap):
    """按阈值把计数切成 run，再把间距 < gap 的 run 并成组。"""
    runs, s = [], None
    for i, v in enumerate(cnt):
        if v >= thr:
            if s is None:
                s = i
        elif s is not None:
            runs.append([s, i - 1]); s = None
    if s is not None:
        runs.append([s, len(cnt) - 1])
    groups = []
    for a, b in runs:
        if groups and a - groups[-1][1] - 1 < gap:
            groups[-1][1] = b
        else:
            groups.append([a, b])
    return groups


def _trim_axis(sub, gap, narrow, light):
    """沿一条轴切掉"又窄又轻、还隔着白缝"的外侧杂块。返回保留区间 (起, 止)。"""
    n = sub.shape[1]
    cnt = sub.sum(axis=0)
    thr = max(2, int(0.03 * sub.shape[0]))
    gs = _group_runs(cnt, thr, gap)
    if len(gs) < 2:
        return 0, n - 1
    mass = [int(cnt[a:b + 1].sum()) for a, b in gs]
    k = int(np.argmax(mass))
    tot = max(1, sum(mass))
    keep = [k]
    for i, (a, b) in enumerate(gs):
        if i == k:
            continue
        if (b - a + 1) <= narrow * n and mass[i] <= light * tot:
            continue                      # 又窄又轻 → 当成杂块
        keep.append(i)
    return min(gs[i][0] for i in keep), max(gs[i][1] for i in keep)


def trim_junk(ink, box, sides="r", gap=6, narrow=0.15, light=0.20, max_cut=0.30):
    """切掉框里贴着字头**右侧**的多余墨块（紧邻的注音「(、下一个字的一角）。

    只在"隔着白缝、又窄又轻"时才切；而且最多切掉 max_cut 比例，防止把淡笔画
    当真杂块切掉。默认只处理右边——上下的断笔多半是真笔画。
    """
    H, W = ink.shape
    x, y = max(0, box["x"]), max(0, box["y"])
    x2, y2 = min(W, box["x"] + box["w"]), min(H, box["y"] + box["h"])
    if x2 - x < 8 or y2 - y < 8:
        return box
    sub = ink[y:y2, x:x2]
    nw, nh = x2 - x, y2 - y
    a, b = _trim_axis(sub, gap, narrow, light)
    nl, nr = 0, nw - 1
    if "l" in sides and a > 0 and a <= max_cut * nw:
        nl = a
    if "r" in sides and b < nw - 1 and (nw - 1 - b) <= max_cut * nw:
        nr = b
    if nl or nr < nw - 1:
        sub = sub[:, nl:nr + 1]
        x += nl
        nw = nr - nl + 1
    if ("t" in sides) or ("b" in sides):
        a2, b2 = _trim_axis(sub.T, gap, narrow, light)
        nt, nb = 0, nh - 1
        if "t" in sides and a2 > 0 and a2 <= max_cut * nh:
            nt = a2
        if "b" in sides and b2 < nh - 1 and (nh - 1 - b2) <= max_cut * nh:
            nb = b2
        if nt or nb < nh - 1:
            sub = sub[nt:nb + 1, :]
            y += nt
            nh = nb - nt + 1
    nw2, nh2 = int(sub.shape[1]), int(sub.shape[0])
    if nw2 == box["w"] and nh2 == box["h"]:
        return box
    return dict(box, x=int(x), y=int(y), w=nw2, h=nh2)


def find_heads(ink, debug=False):
    ink_d = drop_rules(ink)
    ink_d = strip_streaks(ink_d, body_char_height(ink_d))
    bh = body_char_height(ink_d)
    cw = bh
    H, W = ink_d.shape

    # 1a 是 1bit 扫描，淡笔画呈"点线状"（一条横是一串断开的点），连通域会碎成几十块。
    # 只在找连通域这一步用"闭运算后"的图把点线接起来；行距、栏位置、释文检查仍用原图，
    # 免得闭运算改变 bh/行距又把别的判断带偏。
    ink_c = cv2.morphologyEx(ink_d, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    # 全页只做一次连通域，避免按行开窗把跨行的笔画切断
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink_c, connectivity=8)
    comps = [(int(s[0]), int(s[1]), int(s[2]), int(s[3]), int(s[4])) for s in stats[1:]]

    # 先用粗栏估一个行距，只用于后面的尺寸阈值
    lut = np.zeros(n, np.uint8)
    for i, c in enumerate(comps, 1):
        if c[3] <= 1.6 * bh and c[2] <= 3.0 * bh:
            lut[i] = 1
    pitches = []
    for bx0, bx1 in find_bands(lut[lab], bh):
        g = line_grid(ink_d, bx0, bx1, bh)
        if g is not None:
            pitches.append(g[0])
    if not pitches:
        return ([], [], []) if debug else []
    pitch = float(np.median(pitches))

    # 栏的位置由字头自身聚类得到
    margins = column_margins(comps, pitch, cw, W)
    if len(margins) >= 2:
        bands = []
        for i, m in enumerate(margins):
            x1 = margins[i + 1] - 0.35 * cw if i + 1 < len(margins) else W * 0.98
            bands.append((int(max(0, m - 2.0 * cw)), int(x1)))
    else:
        bands = find_bands(lut[lab], bh)

    heads, info, rej = [], [], []
    for bi, (bx0, bx1) in enumerate(bands):
        g = line_grid(ink_d, bx0, bx1, bh, pitch=pitch)
        if g is None:
            continue
        pitch_b, phase, ys, ye = g
        x0 = bx0 + 2.0 * cw if len(margins) >= 2 else col_margin(lut[lab], bx0, bx1, pitch_b, phase, ye)
        info.append(dict(band=bi, x0=x0, x1=bx1, bh=round(bh, 1), pitch=round(pitch_b, 1),
                         phase=phase, nlines=int((ye - phase) // pitch_b)))

        # 候选：宽进严出。扫描件笔画常断成几块，先让碎片都进来，最后按"簇的联合框"
        # 判断是不是字头，否则只按单个连通域的尺寸挑，会只拿到半边的碎片。
        pieces = []
        for x, y, w, h, a in comps:
            if y < ys - 0.45 * pitch or y + h > ye + 0.45 * pitch:
                continue
            if a < 0.005 * pitch * pitch or h < 0.10 * pitch or w < 0.04 * cw:
                continue
            if x + w < x0 - 1.8 * cw or x > x0 + 3.4 * cw:
                continue
            pieces.append([x, y, w, h])

        def _big(b):
            return b[3] > 1.05 * pitch or b[2] > 1.3 * cw

        # 相邻笔画并成一簇。竖直方向只许很小的缝（字头上下相叠，缝大会连到下一条），
        # 水平方向放宽（一个字常断成左右几块）。正文小字之间不许互相连，免得连成长条。
        groups = []
        for b in sorted(pieces, key=lambda b: b[2] * b[3], reverse=True):
            placed = False
            for gr in groups:
                gx = max(gr[0], b[0]) - min(gr[0] + gr[2], b[0] + b[2])
                gy = max(gr[1], b[1]) - min(gr[1] + gr[3], b[1] + b[3])
                if gx > 0.65 * cw or gy > 0.45 * pitch:
                    continue
                u = _union(gr, b)
                if u[3] > 2.05 * pitch or u[2] > 3.0 * cw:
                    continue
                # 笔画被扫断成很多小块时没有"大块"可依附，但只要贴得很紧也允许合并
                if not (_big(gr) or _big(b)):
                    continue
                gr[:] = u
                placed = True
                break
            if not placed:
                groups.append(list(b))

        # 只保留长得像字头的簇
        kept = []
        for x, y, w, h in groups:
            why = None
            if h < 0.12 * pitch or w < 0.22 * pitch:
                why = "flat"
            elif w > 3.2 * cw:            # 通栏的横线、栏线
                why = "wide"
            elif h < 1.1 * pitch and w < 1.35 * cw:
                why = "small"
            elif x > x0 + 3.0 * cw:
                why = "right"
            elif not (x >= x0 - 1.8 * cw and x + w >= x0 + 0.2 * cw):
                why = "left"
            elif h < 1.2 * pitch and w > 2.6 * cw:
                why = "wideflat"          # 又矮又宽：是横排的一串正文小字
            elif _stacked_lines(ink_d[y:y + h, x:x + w], bh, pitch):
                why = "twolines"          # 纵向两条各有一个正文高的墨带：上下两行正文叠起来了
            if why:
                rej.append((x, y, w, h, why))
            else:
                kept.append([x, y, w, h])
        groups = kept

        # 每个簇落到行网格的某一行上，同一行只留最大的一个
        used = {}
        for gr in groups:
            cy = gr[1] + gr[3] / 2.0
            k = int(round((cy - phase) / pitch - 0.85))
            if k < 0:
                rej.append(tuple(gr) + ("offgrid",))
                continue
            ly = phase + k * pitch
            if abs(cy - (ly + 0.85 * pitch)) > 0.6 * pitch:
                rej.append(tuple(gr) + ("offgrid",))
                continue
            # 同一行只留一个簇。字头一定贴着栏左缘，所以优先留离 x0 最近的；
            # 只按面积留最大的，会把字头让给同行里更大的一串正文小字。
            cur = used.get(k)
            if cur is None or (abs(gr[0] - x0), -gr[2] * gr[3]) < (abs(cur[0] - x0), -cur[2] * cur[3]):
                used[k] = gr
        # 同一个字被拆成上下两截、落到相邻两行的情况：合并后总高不超过 2.15 行距才是同一个字
        rows = {}
        for k in sorted(used):
            gr = used[k]
            prev = rows.get(k - 1)
            if prev is not None:
                ox = min(prev[0] + prev[2], gr[0] + gr[2]) - max(prev[0], gr[0])
                gap = max(prev[1] - (gr[1] + gr[3]), gr[1] - (prev[1] + prev[3]), 0)
                u = _union(prev, gr)
                if ox > 0.4 * min(prev[2], gr[2]) and gap <= 0.45 * pitch and u[3] <= 2.15 * pitch:
                    rows[k - 1] = u
                    continue
            rows[k] = gr
        # 自校准：本栏字头的典型高度 gh。明显比 gh 高的框，说明把下一条字头的笔画连进来了，
        # 把它的下沿截到 gh*1.1（字头总在字格顶端，多出来的部分属于下一个字）。
        if rows:
            gh = float(np.median([v[3] for v in rows.values()]))
            cap = max(gh * 1.08, 1.9 * pitch)
            for k, (x, y, w, h) in list(rows.items()):
                if h > cap:
                    rows[k] = [x, y, w, int(round(cap))]
        for k in sorted(rows):
            x, y, w, h = rows[k]
            # 字头右侧、同一垂直范围内必须有释文，否则是横线碎片或空白处的噪点
            rx0 = int(max(x + w, x0 + 2.0 * cw))
            rx1 = int(min(bx1, ink.shape[1]))
            ry0, ry1 = int(y), int(min(y + h, ink.shape[0]))
            if rx1 > rx0 and ry1 > ry0:
                if ink_d[ry0:ry1, rx0:rx1].sum() < 0.006 * (rx1 - rx0) * (ry1 - ry0):
                    rej.append((x, y, w, h, "noref"))
                    continue
            heads.append(dict(x=x, y=y, w=w, h=h, band=bi, line=k,
                              pitch=pitch_b, cw=cw))

        # 补：笔画被打散得太碎、按连通域聚不起来的字头，用"栏左缘通高"信号找回来
        have = [h for h in heads if h["band"] == bi]
        for bx, by, bw, bh2 in margin_heads(ink_d, x0, cw, pitch_b, ys, ye):
            cy, ccy = by + bh2 / 2.0, None
            if _stacked_lines(ink_d[by:by + bh2, bx:bx + bw], bh, pitch_b):
                continue                          # 上下两行正文叠起来的
            if bh2 < 1.2 * pitch_b and bw > 2.6 * cw:
                continue                          # 横排的一串正文小字
            dup = False
            for h in have:
                if abs((h["y"] + h["h"] / 2.0) - cy) < 0.9 * pitch_b:
                    dup = True
                    break
            if dup:
                continue
            k2 = int(round((by + bh2 / 2.0 - phase) / pitch_b - 0.85))
            heads.append(dict(x=bx, y=by, w=bw, h=bh2, band=bi, line=max(0, k2),
                              pitch=pitch_b, cw=cw))
            have.append(heads[-1])

    heads = _merge_split_heads(heads, cw, pitch)
    heads = _merge_vertical_parts(heads, cw, pitch)
    heads = [trim_junk(ink, h) for h in heads]
    heads.sort(key=lambda h: (h["band"], h["y"], h["x"]))
    if debug:
        return dict(heads=heads, bands=bands, info=info, rejected=rej,
                    bh=bh, cw=cw, pitch=pitch)
    return heads


# ---------------------------------------------------------------- 裁图

def crop_square(ink, cx, cy, side):
    """以 (cx, cy) 为中心切 side×side 的正方形，白底黑字；超出页面就留白。"""
    side = int(round(side))
    x0 = int(round(cx - side / 2.0))
    y0 = int(round(cy - side / 2.0))
    H, W = ink.shape
    canvas = np.zeros((side, side), np.uint8)
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1, sy1 = min(W, x0 + side), min(H, y0 + side)
    if sx1 > sx0 and sy1 > sy0:
        canvas[sy0 - y0:sy0 - y0 + (sy1 - sy0), sx0 - x0:sx0 - x0 + (sx1 - sx0)] = ink[sy0:sy1, sx0:sx1]
    return 255 - canvas * 255


def crop_head(ink, box, mode="glyph", margin=0.15):
    """按字头框裁一张正方形小图（白底黑字）。

    mode="glyph"（默认）：边长 = 字的外接框放大 (1+2*margin)=1.30 倍，保证整字在框内、四边留白；
                          极瘦的字（一、丨）有最小边长兜底，不会被裁成一条。
    mode="cell"         ：边长 = 2 行距（全书同尺寸，但高的字可能被切到边）
    """
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    pitch = box.get("pitch", max(w, h))
    if mode == "cell":
        return crop_square(ink, x + w / 2.0, y + h / 2.0, 2.0 * pitch)
    side = max(max(w, h) * (1 + 2 * margin), 1.60 * pitch)
    return crop_square(ink, x + w / 2.0, y + h / 2.0, side)


def trim_box(ink, box):
    """去掉框右端被误并进来的标点碎片（`(`、`“`、`。` 之类）。

    判据很窄：只是一小段（宽 ≤ 0.28 字格宽）、而且和左边最近的墨迹之间有一道
    ≥ 0.30 字格宽的白缝。字本身的笔画不会满足这两条，所以不会误删。
    """
    H, W = ink.shape
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    cw = box.get("cw", float(max(w, h)))
    pitch = box.get("pitch", float(max(w, h)))
    X0, Y0 = max(0, x - int(0.6 * cw)), max(0, y - int(0.12 * pitch))
    Y1 = min(H, y + h + int(0.12 * pitch))
    dust = max(40.0, 0.02 * cw * cw)                  # 扫描噪点，不参与判断
    for _ in range(3):
        if w <= 0:
            break
        sub = ink[Y0:Y1, X0:x + w]
        n, lab, stats, _ = cv2.connectedComponentsWithStats(sub, connectivity=8)
        mask = np.zeros(sub.shape, bool)
        for i in range(1, n):
            if int(stats[i][4]) >= dust:
                mask |= (lab == i)
        if not mask.any():
            break
        col = mask.any(axis=0)
        # 按"墨柱"分段：小于 0.12 字格宽的缝算连着的（一个标点常被打散成几个小点）
        mgap = max(3, int(0.12 * cw))
        runs, s, gap = [], None, 0
        for i, v in enumerate(col):
            if v:
                if s is None:
                    s = i
                gap = 0
            elif s is not None:
                gap += 1
                if gap > mgap:
                    runs.append((s, i - gap))
                    s = None
                    gap = 0
        if s is not None:
            runs.append((s, len(col) - 1))
        if len(runs) < 2:
            break
        a0, b0 = runs[-1]
        area = int(mask[:, a0:b0 + 1].sum())
        main = max(int(mask[:, a:b + 1].sum()) for a, b in runs[:-1])
        rows = np.nonzero(mask[:, a0:b0 + 1].any(axis=1))[0]
        ycen = (float(rows[0] + rows[-1]) / 2.0 / mask.shape[0]) if len(rows) else 0.0
        ih = (rows[-1] - rows[0] + 1) if len(rows) else 0
        gapw = (a0 - runs[-2][1]) / cw
        # 只有当它是"又窄、又矮、又明显比主体小"的东西，才当标点删掉；
        # 标点离字会有一点白缝，另外它还总落在字的中下部（字的点画在上部）。
        if (b0 - a0 + 1) > 0.70 * cw or area > 0.25 * main or ih > 0.55 * h:
            break
        if gapw < 0.35 and not (gapw >= 0.10 and ycen >= 0.35):
            break
        neww = runs[-2][1] + 1 + X0 - x
        if neww < 0.55 * cw:
            break
        # 要删的东西必须明显小于保留的部分；否则宁可留着（多一块总比缺一笔好）
        if neww < 1.5 * (b0 - a0 + 1):
            break
        w = int(neww)
    return dict(box, w=int(w))


def page_side(heads):
    """一页统一的方图边长：字头最大的那批放大 1.3 倍，且不小于 2 行距。"""
    if not heads:
        return 200
    pitch = float(np.median([h["pitch"] for h in heads]))
    size = float(np.percentile([max(h["w"], h["h"]) for h in heads], 95))
    size = min(size, 2.5 * pitch)                     # 抑制个别误检框对整页尺度的影响
    return int(round(1.30 * max(2.0 * pitch, size)))


def grow_vertical(ink, box, pitch):
    """把框上下沿长到把字包全。

    字框偶尔会在上下方向卡进笔画中间（比如 `三` 的最后一横），一硬裁就"下面少一笔"。
    只在本框的横向范围内看：行内有墨就继续往外长，遇到 >0.15 行距的白缝就停，
    最多长 0.45 行距（再远就是下一行正文了）。左右不长，那是为了让标点进不来。
    """
    H = ink.shape[0]
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    col = ink[:, x:x + w]
    row = col.any(axis=1)
    gtol = max(2, int(0.22 * pitch))
    lim = int(0.32 * pitch)
    top, gap = y, 0
    while top > 0 and (y - top) < lim:
        if row[top - 1]:
            top -= 1
            gap = 0
        else:
            gap += 1
            if gap > gtol:
                break
            top -= 1
    bot, gap = y + h, 0
    while bot < H - 1 and (bot - (y + h)) < lim:
        if row[bot]:
            bot += 1
            gap = 0
        else:
            gap += 1
            if gap > gtol:
                break
            bot += 1
    # 往上长容易被"上一行正文的底边"骗：那是散点，任何一行都不连续。真笔画总有一行是连成一条的。
    if top < y:
        band = ink[top:y, x:x + w]
        best = 0
        for r in band:
            cur = 0
            for v in r:
                cur = cur + 1 if v else 0
                if cur > best:
                    best = cur
        if best < 0.60 * w:
            top = y
    # 长的时候会跨过白缝，最后收回到真正的墨迹行
    while top < bot and not row[top]:
        top += 1
    while bot > top and not row[bot - 1]:
        bot -= 1
    # 字头最多占两行多一点，长过头就说明摸到下一行正文了
    cap = int(round(2.10 * pitch))
    if bot - top > cap:
        over = (bot - top) - cap
        up = min(top - y, over // 2)
        top -= up
        bot = top + cap
    return dict(box, y=int(top), h=int(max(h, bot - top)))


def trim_bottom(ink, box, pitch, cw):
    """去掉框底下蹭进来的下一行正文碎块。

    判据和右边的标点裁剪同一套：底端那一条必须又矮、又明显比主体小、且和主体之间有白缝。
    """
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    sub = ink[y:y + h, x:x + w]
    if sub.size == 0:
        return box
    row = sub.any(axis=1)
    gtol = max(2, int(0.12 * pitch))
    runs, s, gap = [], None, 0
    for i, v in enumerate(row):
        if v:
            if s is None:
                s = i
            gap = 0
        elif s is not None:
            gap += 1
            if gap > gtol:
                runs.append((s, i - gap))
                s = None
                gap = 0
    if s is not None:
        runs.append((s, len(row) - 1))
    if len(runs) < 2:
        return box
    a0, b0 = runs[-1]
    area = int(sub[a0:b0 + 1, :].sum())
    main = max(int(sub[a:b + 1, :].sum()) for a, b in runs[:-1])
    if (b0 - a0 + 1) > 0.40 * pitch or area > 0.25 * main:
        return box
    if a0 - runs[-2][1] < 0.12 * pitch:
        return box
    nh = runs[-2][1] + 1
    if nh < 1.15 * pitch:
        return box
    return dict(box, h=int(nh))


def head_crop(ink, box, side=None):
    """一个字头的最终成图：外扩切正方形，再抹掉不属于这个字的墨迹。

    side 给定时用它（同页所有字头同尺寸），否则按字框自己算。
    """
    pitch = box.get("pitch", max(box["w"], box["h"]))
    cw = box.get("cw", max(box["w"], box["h"]))
    box = trim_box(ink, box)
    box = trim_bottom(ink, box, pitch, cw)
    box = grow_vertical(ink, box, pitch)
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    pitch = box.get("pitch", max(w, h))
    if side is None:
        side = int(round(max(max(w, h) * 1.30, 2.0 * pitch)))
    side = int(round(side))
    cx, cy = x + w / 2.0, y + h / 2.0
    left, top = cx - side / 2.0, cy - side / 2.0
    img = crop_square(ink, cx, cy, side)
    keep = ((x - left) / side, (y - top) / side,
            (x + w - left) / side, (y + h - top) / side)
    keep = (max(0.0, keep[0]), max(0.0, keep[1]), min(1.0, keep[2]), min(1.0, keep[3]))
    return clean_crop(img, keep, tol=0.0, pad_frac=0.06)


def clean_crop(img, keep_rect=None, tol=0.05, pad_frac=0.09):
    """把方图里"不属于这个字"的墨迹抹白，保证框里只有这个字、四边是白的。

    keep_rect：字在框里的归一化范围 (x0, y0, x1, y1)。给了就只保留与它相交的连通块。
    pad_frac ：先把外圈 pad_frac 的边带整条抹白（字按 1.3 倍尺寸居中，边带碰不到字）。
    """
    ink = ((255 - img) > 127).astype(np.uint8)
    h, w = ink.shape
    pad = int(round(pad_frac * min(h, w)))
    if pad > 0:
        ink[:pad, :] = 0
        ink[-pad:, :] = 0
        ink[:, :pad] = 0
        ink[:, -pad:] = 0
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    if n <= 1:
        return 255 - ink * 255
    out = ink.copy()
    if keep_rect is not None:
        kx0 = max(0, int((keep_rect[0] - tol) * w))
        ky0 = max(0, int((keep_rect[1] - tol) * h))
        kx1 = min(w, int((keep_rect[2] + tol) * w))
        ky1 = min(h, int((keep_rect[3] + tol) * h))
        for i in range(1, n):
            x, y, ww, hh, _ = (int(v) for v in stats[i])
            cxx = x + ww / 2.0
            # 与字框相交的整块保留；但块心落在右边界外的，是紧贴右边的标点，抹掉
            if x >= kx1 or y >= ky1 or x + ww <= kx0 or y + hh <= ky0 or cxx > kx1:
                out[lab == i] = 0
        # 有些字和释文在扫描件上是连在一起的，按连通块剔不掉，这里直接把框外硬清零
        out[:ky0, :] = 0
        out[ky1:, :] = 0
        out[:, :kx0] = 0
        out[:, kx1:] = 0
    min_area = max(4.0, 0.0006 * w * h)
    for i in range(1, n):
        if int(stats[i][4]) < min_area:
            out[lab == i] = 0
    return 255 - out * 255
