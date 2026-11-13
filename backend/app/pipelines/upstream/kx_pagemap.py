"""建立「kangxi-1716 的 PDF 页序 → 新下载 300dpi 扫描的页序」映射。

上游 `sources/manifest.json` 每条字头记录都带该字在扫描页上的矩形；把矩形从新 PDF 的
每一页裁出来，与上游交付的净字 PNG 比 IoU，得分最高的页就是上游那一页。
偏移按册内单调递增分段（新扫描在各集前多了卷首/封面），所以对每册抽样若干页求出
一系列 (上游页, 新页序) 锚点，再插值出整册映射。

产出（_up/，不入库）：_up/kangxi/cache/pagemap_XX.json  {"上游页": 新页序}
用法：python tools/kx_pagemap.py [册号 ...]      # 不给册号就做全部 01-13
"""
import collections
import glob
import io
import json
import os
import sys

import numpy as np
import pymupdf
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UP = os.path.join(ROOT, "_up", "kangxi")
BOOK = os.path.join(UP, "book")
GLYPHS = os.path.join(UP, "glyphs-src")
MANIFEST = os.path.join(UP, "kangxi-1716", "sources", "manifest.json")
CACHE = os.path.join(UP, "cache")

PAD = 10          # 净字 PNG 四周的白边
ANCHORS = 24      # 每册抽样页数


def load_records():
    """(册, 上游PDF页) → 该页面积最大的字头记录。"""
    raw = json.load(open(MANIFEST, encoding="utf-8"))
    per_page = {}
    for r in raw:
        if r["\u7c7b\u522b"] != "\u5b57\u5934":
            continue
        vol = str(r["\u518c"]).zfill(2)
        page = int(r["PDF\u9875"])
        x0, y0 = int(r["\u9875\u9762x0"]), int(r["\u9875\u9762y0"])
        x1, y1 = int(r["\u9875\u9762x1"]), int(r["\u9875\u9762y1"])
        area = (x1 - x0) * (y1 - y0)
        key = (vol, page)
        if key not in per_page or area > per_page[key]["area"]:
            per_page[key] = {
                "vol": vol, "page": page, "area": area,
                "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "file": r["\u6587\u4ef6"],
            }
    return per_page


def glyph_mask(rec, w, h):
    path = os.path.join(GLYPHS, rec["file"])
    if not os.path.exists(path):
        return None
    im = Image.open(path).convert("L")
    im = im.crop((PAD, PAD, im.width - PAD, im.height - PAD)).resize((w, h))
    return np.array(im) < 128


def volume_pdf(vol):
    for p in glob.glob(os.path.join(BOOK, "*.pdf")):
        if os.path.basename(p).split(".")[1] == vol:
            return p
    return None


def page_gray(doc, idx):
    if idx < 0 or idx >= doc.page_count:
        return None
    imgs = doc[idx].get_images(full=True)
    if not imgs:
        return None
    best = max(imgs, key=lambda i: i[2] * i[3])
    raw = doc.extract_image(best[0])
    img = Image.open(io.BytesIO(raw["image"])).convert("L")
    return np.array(img) < 128, img.size


def compress(anchors):
    """锚点 → 偏移段 [(起始上游页, 偏移)]，只按偏移变化切段。"""
    runs = []
    for p, off, s in anchors:
        if runs and runs[-1][1] == off:
            continue
        runs.append((p, off))
    # 抖动：只有单页反复的段合并回上一段（偏移必须单调不减）
    out = []
    for p, off in runs:
        if out and off < out[-1][1]:
            continue
        out.append((p, off))
    return out


def refine_boundary(doc, per_page, vol, inner_pages, off_prev, off_next):
    """在一段不确定区间里逐页试两种偏移，返回 (逐页映射, 是否全部成功)。"""
    mask_cache = {}

    def page_mask(idx):
        if idx in mask_cache:
            return mask_cache[idx]
        got = page_gray(doc, idx)
        mask_cache[idx] = None if got is None else got
        return mask_cache[idx]

    mapping = {}
    for p in inner_pages:
        rec = per_page[(vol, p)]
        tpl = glyph_mask(rec, rec["x1"] - rec["x0"], rec["y1"] - rec["y0"])
        if tpl is None:
            mapping[p] = p + off_prev
            continue
        best = (-1.0, p + off_prev)
        for off in (off_prev, off_next):
            got = page_mask(p + off)
            if got is None:
                continue
            arr, (W, H) = got
            if rec["y1"] >= H or rec["x1"] >= W:
                continue
            crop = arr[rec["y0"]:rec["y1"], rec["x0"]:rec["x1"]]
            if crop.shape != tpl.shape:
                continue
            u = (crop | tpl).sum()
            if not u:
                continue
            s = float((crop & tpl).sum()) / float(u)
            if s > best[0]:
                best = (s, p + off)
        mapping[p] = best[1]
    return mapping


def build_volume(vol, per_page):
    path = volume_pdf(vol)
    if not path:
        print("册 %s 没有 PDF" % vol)
        return None
    pages = sorted(p for (v, p) in per_page if v == vol)
    if not pages:
        return None
    step = max(1, len(pages) // ANCHORS)
    anchors = pages[::step][:ANCHORS]

    targets = []
    for p in anchors:
        rec = per_page[(vol, p)]
        w, h = rec["x1"] - rec["x0"], rec["y1"] - rec["y0"]
        tpl = glyph_mask(rec, w, h)
        if tpl is not None:
            targets.append((p, rec, tpl))
    if not targets:
        return None

    doc = pymupdf.open(path)
    best = {p: (-1.0, None) for p, _, _ in targets}
    n = doc.page_count
    for idx in range(n):
        got = page_gray(doc, idx)
        if got is None:
            continue
        arr, (W, H) = got
        for p, rec, tpl in targets:
            if rec["y1"] >= H or rec["x1"] >= W:
                continue
            crop = arr[rec["y0"]:rec["y1"], rec["x0"]:rec["x1"]]
            if crop.shape != tpl.shape:
                continue
            u = (crop | tpl).sum()
            if not u:
                continue
            s = float((crop & tpl).sum()) / float(u)
            if s > best[p][0]:
                best[p] = (s, idx)

    hits = sorted((p, v[1], v[0]) for p, v in best.items() if v[1] is not None and v[0] >= 0.45)
    if not hits:
        print("册 %s 一页都没对上" % vol)
        doc.close()
        return None
    runs = compress([(p, idx - p, s) for p, idx, s in hits])
    # 段边界落在两个锚点之间：把这段区间逐页试两种偏移，钉死边界
    mapping = {}
    for i, (start, off) in enumerate(runs):
        end = runs[i + 1][0] - 1 if i + 1 < len(runs) else max(pages)
        if i + 1 < len(runs):
            nxt_off = runs[i + 1][1]
            inner = [p for p in pages if start <= p <= end and (vol, p) in per_page]
            mapping.update(refine_boundary(doc, per_page, vol, inner, off, nxt_off))
            for p in range(start, end + 1):
                mapping.setdefault(p, p + off)
        else:
            for p in range(start, end + 1):
                mapping[p] = p + off
    doc.close()

    os.makedirs(CACHE, exist_ok=True)
    json.dump({"vol": vol, "pdf_pages": n,
               "anchors": [{"up": p, "new": idx, "iou": round(s, 3)} for p, idx, s in hits],
               "runs": runs,
               "map": {str(k): v for k, v in sorted(mapping.items())}},
              open(os.path.join(CACHE, "pagemap_%s.json" % vol), "w"), ensure_ascii=False)
    print("册 %s：%d 页，锚点 %d/%d，偏移段 %s"
          % (vol, n, len(hits), len(targets), runs))
    return mapping


def main():
    per_page = load_records()
    vols = sys.argv[1:] or sorted({v for v, _ in per_page})
    for vol in vols:
        build_volume(vol, per_page)


if __name__ == "__main__":
    main()
