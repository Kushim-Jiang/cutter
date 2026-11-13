#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读出《汉字海》每个条目行上的字段：印刷条目号、部首、笔画数、U 组码位。

版面（一条条目）：

    ┌─────────────────────────────┐
    │ 广            9              │  ← 字头 + 印刷条目号
    │   一部  3画  Ⓤ5E7F          │  ← 属性行：部首·部 · N画 · Ⓤ码位
    │   音未详……                   │
    └─────────────────────────────┘

做法：把一页所有条目的「条目号行 + 属性行」窗口裁下来拼成一条竖长图，**一页只做一次
OCR**（RapidOCR，本地 ONNX，不联网），再按文本块的纵坐标归位到条目、按行挑出属性行，
从里面挖 部首 / 笔画 / 码位、从上一行挖条目号。

    python tools/hzh_fields.py                  # 全量 -> _zihai/hh/fields.ndjson
    python tools/hzh_fields.py --check 452      # 打印某页结果，肉眼核对
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hzh_crop as C  # noqa: E402

STRIP_SCALE = 0.6               # 属性行长图放大倍数（RapidOCR 内部会缩放，放大反而丢小字）
GAP = 8                         # 长图里两条之间的白缝
DET_LIMIT = 384                 # 检测网络的限定边长：长图很长，736 要 15s/页，384 只要 5s 且块数不变

# 属性行：`<部首>部  <N>画  <圈号><码位>`；圈号在 OCR 里可能是 U / ① / O / Ⓤ
PAT_CODE = re.compile(r"([0-9A-Fa-f]{3,6})\s*$")
PAT_STROKE = re.compile(r"(\d{1,2})\s*画")
BLOCK_RAD = re.compile(r"([^\d]{1,2})部")
CIRCLED = "UuOo①①ⓊⓊΦφ〇0O"


# ---------------------------------------------------------------- 版面

def page_geom(pdf, pno):
    raw = C.load_ink(pdf, pno)
    heads, pitch, margins, _sus = C.page_boxes(raw)
    return raw.astype(np.float32), heads, pitch, margins


# ---------------------------------------------------------------- 属性行 OCR

def attr_window(heads, pitch):
    """每个条目的裁切窗口 (seq, y0, y1)：字头顶往上 0.7p、往下 2.2p，但不越过下一条目的条目号行。

    属性行不一定紧贴字头（有的字头很高），窗口放宽后让解析器自己挑属性行。
    索引里的框可能已收紧到墨迹（hzh_recut --tighten），y 是墨迹顶而不是字格顶；
    两种取法的窗口实测差异很小（抽查三页条目号正确率都是 99.1%），所以直接用 y。
    """
    by_col = {}
    for i, h in enumerate(heads, 1):
        by_col.setdefault(h["col"], []).append((i, h))
    win = {}
    for _ci, lst in by_col.items():
        lst.sort(key=lambda p: p[1]["y"])
        for k, (i, h) in enumerate(lst):
            lo = int(h["y"] - 0.70 * pitch)
            hi = int(h["y"] + 2.20 * pitch)
            if k + 1 < len(lst):
                hi = min(hi, int(lst[k + 1][1]["y"] - 0.60 * pitch))
            win[i] = (lo, max(lo + int(1.2 * pitch), hi))
    return win


def ocr_strip(ocr, raw, items, pitch, with_box=False):
    """把若干窗口拼成一条竖长图，一次 OCR。

    items: [(x0, y0, y1, x右)]；返回与 items 等长的列表，每项是该窗口里的
    [(y 相对窗口顶, x, 文本)]（with_box=True 时给 {y, x, w, h, text}）。
    """
    if not items:
        return []
    items = [(x0, y0, max(y1, y0 + 8), max(x1, x0 + 8)) for (x0, y0, y1, x1) in items]
    width = max(x1 - x0 for x0, _y0, _y1, x1 in items)
    tw = max(2, int(round(width * STRIP_SCALE)))
    parts, spans, ycur = [], [], 0
    for (x0, y0, y1, x1) in items:
        y0 = max(0, y0)
        y1 = min(raw.shape[0], y1)
        sub = (1.0 - raw[y0:y1, x0:x1]) * 255.0
        th = max(2, int(round(sub.shape[0] * STRIP_SCALE)))
        sub = cv2.resize(sub, (int(round((x1 - x0) * STRIP_SCALE)), th), interpolation=(
            cv2.INTER_AREA if STRIP_SCALE < 1 else cv2.INTER_CUBIC))
        right = tw - sub.shape[1]
        if right > 0:
            sub = cv2.copyMakeBorder(sub, 0, 0, 0, right, cv2.BORDER_CONSTANT, value=255)
        parts.append(sub.astype(np.uint8))
        parts.append(np.full((GAP, tw), 255, np.uint8))
        spans.append((ycur, ycur + sub.shape[0]))
        ycur += sub.shape[0] + GAP
    strip = np.vstack(parts)
    out = [[] for _ in items]
    try:
        res, _ = ocr(cv2.cvtColor(strip, cv2.COLOR_GRAY2BGR), box_thresh=0.2, text_score=0.3)
    except Exception:                    # 个别窗口把 OCR 喂崩了，别拖垮整页
        return out
    for r in (res or []):
        box, txt = r[0], r[1]
        yc = (box[0][1] + box[2][1]) / 2.0
        xc = (box[0][0] + box[2][0]) / 2.0
        k, best = None, 1e9
        for i, (a, b) in enumerate(spans):
            d = 0.0 if a <= yc <= b else min(abs(yc - a), abs(yc - b))
            if d < best:
                best, k = d, i
        if k is None or best > 0.5 * pitch * STRIP_SCALE:
            continue
        if with_box:
            s = 1.0 / STRIP_SCALE
            out[k].append(dict(y=(yc - spans[k][0]) * s, x=xc * s,
                               w=(box[2][0] - box[0][0]) * s, h=(box[2][1] - box[0][1]) * s,
                               text=txt))
        else:
            out[k].append((yc - spans[k][0], xc, txt))
    return out


def ocr_page_attr(ocr, raw, heads, pitch, margins):
    """一页所有条目的「条目号行 + 属性行」窗口 → 一次 OCR；返回 {seq: [(y, x, 文本)]}。

    窗口从条目号行上方一直到字头下 2.2p（或下一条目的条目号行），把属性行、
    别名框（如 `[①於]`）、条目号一起框进来 —— 检测器比几何更会挑行，
    解析时再按"像属性行"过滤。
    """
    right_default = int(raw.shape[1] * 0.98)
    win = attr_window(heads, pitch)
    seqs, items = [], []
    for i, h in enumerate(heads, 1):
        ci = h["col"]
        cr = int(margins[ci + 1] - 0.3 * pitch) if ci + 1 < len(margins) else right_default
        y0, y1 = win[i]
        seqs.append(i)
        items.append((int(h["x0"] + 0.95 * pitch), max(0, y0), min(raw.shape[0], y1), cr))
    return dict(zip(seqs, ocr_strip(ocr, raw, items, pitch)))


def _rows(blocks, tol):
    """按纵坐标把文本块聚成行。"""
    rows = []
    for y, x, txt in sorted(blocks, key=lambda b: (b[0], b[1])):
        for r in rows:
            if abs(r["y"] - y) <= tol:
                r["b"].append((x, txt))
                break
        else:
            rows.append(dict(y=y, b=[(x, txt)]))
    for r in rows:
        r["txt"] = " ".join(t for _x, t in sorted(r["b"]))
    return rows


def parse_attr(blocks, pitch=91.0):
    """一个条目的全部 OCR 块 → (部首, 笔画, 码位)。

    块里既有条目号、属性行，也可能有正文。先聚成行，再挑「像属性行」的那一行：
    含 `N画` 记 2 分、有 `X部` 记 2 分、整行能扣出码位记 1 分，取最上面且 ≥2 分的行。
    属性行必须紧跟在条目号行下面，所以从上往下扫到第一行像样的就停。
    """
    rows = _rows(blocks, tol=max(20.0, 0.35 * pitch))
    best = None
    for r in rows:
        txt = r["txt"].replace("\u3000", " ")
        score = 0
        if PAT_STROKE.search(txt.replace("面", "画").replace("田", "画")):
            score += 2
        if BLOCK_RAD.search(txt):
            score += 2
        if _hex_tail(txt):
            score += 1
        if score >= 2:
            best = r
            break
    if best is None:
        return None, None, None
    txt = best["txt"].replace("\u3000", " ").replace("面", "画").replace("田", "画")
    strokes = code = radical = None
    m = PAT_STROKE.search(txt)
    if m:
        strokes = int(m.group(1))
    m = BLOCK_RAD.search(txt)
    if m:
        head = m.group(1)
        head = "".join(ch for ch in head if ch not in CIRCLED)
        head = re.sub(r"[^\u3400-\u9fff\uf900-\ufaff]", "", head)
        if head:
            radical = head[-1]
    code = _hex_tail(txt)
    return radical, strokes, code


def parse_entry_num(blocks, pitch):
    """OCR 块里的印刷条目号：从上往下第一行里那个纯数字块。"""
    for r in _rows(blocks, tol=max(20.0, 0.35 * pitch)):
        if PAT_STROKE.search(r["txt"]) or BLOCK_RAD.search(r["txt"]):
            break
        for _x, t in sorted(r["b"]):
            t = t.strip().replace("\u3000", "")
            if 1 <= len(t) <= 6 and all(ch in "0123456789" for ch in t):
                return int(t)
    return None


def _hex_tail(txt):
    """从一行里扣出码位（`U 4E00` / `①2011E` / `2画·①20001`）。"""
    t = txt.replace(" ", "")
    t = t.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1")
    m = re.search(r"([0-9A-Fa-f]{4,6})$", t)
    if not m:
        return None
    cand = m.group(1).upper()
    if not any(ch in "0123456789" for ch in cand[-4:]):
        return None
    return cand


# ---------------------------------------------------------------- 每页

def limit_onnx_threads(n):
    """限制 onnxruntime 的线程数。

    默认每个进程开 28 个 intra-op 线程，多进程并行时会互相打架（实测 8 进程比
    单进程还慢一倍）。在实例化 RapidOCR 之前改掉它。
    """
    import onnxruntime as ort
    import rapidocr_onnxruntime.utils as ru

    base = ort.SessionOptions

    class _SO(base):
        def __init__(self):
            super().__init__()
            self.intra_op_num_threads = n
            self.inter_op_num_threads = 1

    ru.SessionOptions = _SO


def heads_from_index(path, pno, pitch, margins):
    """直接拿索引里的框当字头（补切出来的框不在自动检测结果里，只能这么喂）。"""
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            o = json.loads(line)
            if o["page"] != pno:
                continue
            ci = o["col"] - 1
            rows.append(dict(seq=o["seq"], col=ci, x=o["x"], y=o["y"], w=o["w"], h=o["h"],
                             x0=int(margins[ci]) if ci < len(margins) else o["x"]))
    rows.sort(key=lambda r: r["seq"])
    return rows


def work(args):
    pdf, pno, onnx_threads, heads_path = args
    cv2.setNumThreads(1)
    if onnx_threads:
        limit_onnx_threads(onnx_threads)
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()
    for op in ocr.text_detector.preprocess_op:
        if hasattr(op, "limit_side_len"):
            op.limit_side_len = DET_LIMIT
    raw, heads, pitch, margins = page_geom(pdf, pno)
    if heads_path:
        got = heads_from_index(heads_path, pno, pitch, margins)
        if got:
            heads = got
    texts = ocr_page_attr(ocr, raw, heads, pitch, margins)
    rows = []
    for i, h in enumerate(heads, 1):
        seq = h.get("seq") or i
        ci = h["col"]
        entry = parse_entry_num(texts.get(seq, []), pitch)
        radical, strokes, code = parse_attr(texts.get(seq, []), pitch)
        rows.append(dict(page=pno, seq=seq, col=ci + 1, x=h["x"], y=h["y"], w=h["w"], h=h["h"],
                         entry=entry, radical=radical, strokes=strokes, code=code))
    return pno, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default=C.PDF)
    ap.add_argument("--start", type=int, default=C.FIRST_PAGE)
    ap.add_argument("--end", type=int, default=C.LAST_PAGE)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="", help="输出目录（必填；原硬编码路径已移除）")
    ap.add_argument("--jobs", type=int, default=min(14, max(1, (os.cpu_count() or 2) // 2)))
    ap.add_argument("--onnx-threads", type=int, default=0, help="每个进程的 onnxruntime 线程数，0=按 jobs 自动")
    ap.add_argument("--check", type=int, default=0)
    ap.add_argument("--pages-file", default="",
                    help="只跑这些页（每行一个页号，或逗号分隔），写到 fields_fix_new.ndjson")
    ap.add_argument("--merge-fix", action="store_true",
                    help="把 fields_fix_new.ndjson 并进 fields_fix.ndjson（按页覆盖）")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    if args.check:
        pno, rows = work((args.pdf, args.check, 0, ""))
        for r in rows:
            print(f"{r['seq']:3d} 条目 {str(r['entry']):>7}  部首 {str(r['radical']):<3}"
                  f" 笔画 {str(r['strokes']):>3}  码位 {str(r['code']):>7}")
        return

    if args.merge_fix:
        new_p = os.path.join(args.out, "fields_fix_new.ndjson")
        fix_p = os.path.join(args.out, "fields_fix.ndjson")
        old = {}
        if os.path.exists(fix_p):
            with open(fix_p, encoding="utf-8") as fh:
                old = {(r["page"], r["seq"]): r for r in (json.loads(line) for line in fh)}
        new = {}
        with open(new_p, encoding="utf-8") as fh:
            new = {(r["page"], r["seq"]): r for r in (json.loads(line) for line in fh)}
        pages_done = {p for p, _s in new}          # 这些页整页替换
        merged = {k: v for k, v in old.items() if k[0] not in pages_done}
        merged.update(new)
        with open(fix_p, "w", encoding="utf-8", newline="\n") as fh:
            for k in sorted(merged):
                fh.write(json.dumps(merged[k], ensure_ascii=False) + "\n")
        print(f"合并 {len(pages_done)} 页，共 {len(merged)} 行 -> {fix_p}")
        return

    pages = list(range(args.start, min(args.end, C.LAST_PAGE) + 1))
    if args.pages_file:
        with open(args.pages_file, encoding="utf-8") as fh:
            pages = sorted({int(t) for line in fh for t in line.replace(",", " ").split() if t})
    if args.limit:
        pages = pages[:args.limit]
    heads = os.path.join(args.out, "index.ndjson") if args.pages_file else ""
    t0 = time.time()
    total = 0
    ok = dict(entry=0, radical=0, strokes=0, code=0)
    out_path = os.path.join(args.out, "fields_fix_new.ndjson" if args.pages_file else "fields.ndjson")
    miss_path = os.path.join(args.out, "fields_miss.ndjson")
    ex = None
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh, \
            open(miss_path, "w", encoding="utf-8", newline="\n") as mh:
        try:
            if args.jobs > 1:
                thr = args.onnx_threads or max(1, min(2, (os.cpu_count() or 4) // args.jobs))
                ex = ProcessPoolExecutor(max_workers=args.jobs)
                it = ex.map(work, [(args.pdf, p, thr, heads) for p in pages], chunksize=1)
            else:
                it = (work((args.pdf, p, 0, heads)) for p in pages)
            for n, (pno, rows) in enumerate(it, 1):
                for r in rows:
                    total += 1
                    for k in ok:
                        ok[k] += r[k] is not None
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                    if r["radical"] is None or r["strokes"] is None or r["code"] is None:
                        mh.write(json.dumps(dict(page=pno, seq=r["seq"], entry=r["entry"],
                                                 radical=r["radical"], strokes=r["strokes"],
                                                 code=r["code"]), ensure_ascii=False) + "\n")
                if n % 50 == 0:
                    el = time.time() - t0
                    pc = lambda v: f"{100.0 * v / max(1, total):.1f}%"
                    print(f"  {n}/{len(pages)} 页 {total} 框  条目号 {pc(ok['entry'])}  "
                          f"部首 {pc(ok['radical'])}  笔画 {pc(ok['strokes'])}  码位 {pc(ok['code'])}"
                          f"  剩余 {el / n * (len(pages) - n) / 60:.1f}min", flush=True)
        finally:
            if ex is not None:
                ex.shutdown()
    pc = lambda v: f"{100.0 * v / max(1, total):.1f}%"
    print(f"完成 {len(pages)} 页 {total} 框：条目号 {pc(ok['entry'])}  部首 {pc(ok['radical'])}  "
          f"笔画 {pc(ok['strokes'])}  码位 {pc(ok['code'])}  用时 {(time.time() - t0) / 60:.1f}min")
    print("->", out_path)


if __name__ == "__main__":
    main()
