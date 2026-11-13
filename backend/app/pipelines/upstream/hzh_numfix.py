#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把读丢了的印刷条目号再读一遍。

`hzh_fields` 是"一条竖长图一次 OCR"：窗口里除了号码还有属性行、释义行，号码只占窗口
顶上一小块，所以有 5 千来个框读出来是空的（0/None）。排号（`hzh_ziid`）就靠这些读数
定位页内跳号，少一个读数就会把"哪里漏了 1 个号"算错一格 —— 表现是"478 缺号"，其实
478 有框，是前面某个号被算漏了。

这里只裁号码那一小块（字头右侧 0.05–2.6 行距、顶上 0–0.8 行距），逐页一条长图重读，
写 `fields_num.ndjson`；`hzh_ziid` 在原来的读数为空时才用它。

    python tools/hzh_numfix.py --out _zihai/hh
"""
import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hzh_crop as C  # noqa: E402
import hzh_fields as F  # noqa: E402


def read_ndjson(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_known(data):
    """已有读数：{(page, seq): entry or None}。"""
    known = {}
    for name in ("fields.ndjson", "fields_fix.ndjson"):
        p = os.path.join(data, name)
        if os.path.exists(p):
            known.update({(r["page"], r["seq"]): r.get("entry") for r in read_ndjson(p)})
    return known


def read_number(blocks):
    """窗口里最像条目号的那个纯数字块。"""
    best = None
    for b in blocks:
        t = b["text"].strip().replace("\u3000", "").replace(" ", "")
        t = t.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1")
        if not t or not all(ch in "0123456789" for ch in t) or not 1 <= len(t) <= 6:
            continue
        if best is None or b["y"] < best[0]:        # 号码在最上面那一行
            best = (b["y"], int(t))
    return best[1] if best else None


def num_page(args):
    pdf, pno, rows, onnx_threads = args
    raw = C.load_ink(pdf, pno)
    pitch = float(rows[0].get("pitch") or 91)
    items, want = [], []
    for r in rows:
        x1 = int(r["x"] + r["w"])
        items.append((x1 + int(0.05 * pitch), int(r["y"] - 0.10 * pitch),
                      int(r["y"] + 0.80 * pitch), x1 + int(2.60 * pitch)))
        want.append(r["seq"])
    F.limit_onnx_threads(onnx_threads)
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()
    for op in ocr.text_detector.preprocess_op:
        if hasattr(op, "limit_side_len"):
            op.limit_side_len = F.DET_LIMIT
    blocks = F.ocr_strip(ocr, raw, items, pitch, with_box=True)
    out = []
    for seq, blks in zip(want, blocks):
        n = read_number(blks)
        if n:
            out.append(dict(page=pno, seq=seq, entry=n))
    return out


def main():
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--data", default=os.path.join(here, "_zihai", "hh"))
    ap.add_argument("--pdf", default=C.PDF)
    ap.add_argument("--jobs", type=int, default=min(12, max(1, (os.cpu_count() or 2) // 2)))
    ap.add_argument("--onnx-threads", type=int, default=0)
    args = ap.parse_args()

    known = load_known(args.data)
    pages = {}
    for r in read_ndjson(os.path.join(args.data, "index.ndjson")):
        if known.get((r["page"], r["seq"]), None):
            continue                                  # 已经有读数，不动
        pages.setdefault(r["page"], []).append(r)
    todo = sorted(pages)
    n = sum(len(pages[p]) for p in todo)
    print(f"读数缺失的框 {n} 个，分布在 {len(todo)} 页")
    if not todo:
        return
    threads = args.onnx_threads or max(1, 4 // max(1, args.jobs // 4))
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        res = list(ex.map(num_page, [(args.pdf, p, pages[p], threads) for p in todo]))
    out = [r for rs in res for r in rs]
    path = os.path.join(args.data, "fields_num.ndjson")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"补读 {len(out)}/{n} 个号码 -> {path}")


if __name__ == "__main__":
    main()
