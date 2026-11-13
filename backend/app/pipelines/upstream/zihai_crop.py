#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从《中华字海》扫描 PDF 里切出每个字条目的字头大字图。

用法：
    python tools/zihai_crop.py --start 84 --end 1864 --out _zihai/heads
    python tools/zihai_crop.py --start 84 --limit 10 --out _zihai/heads

源文件用高清扫描（4423x6210 的 1bit 图），路径由调用方给出，
1a 的页号 = 印刷页号 + 83，正文是 1a 第 84~1864 页（印刷 1~1781 页）。

输出：
    <out>/<印刷页:04d>/<印刷页:04d>_<栏:1-3>_<序号:02d>.png   正方形留白的字头
    <out>/index.ndjson                                        每张图的页/栏/位置
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import cv2
import pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zihai_grid import load_ink, find_heads, head_crop, page_side, trim_box  # noqa: E402

DEFAULT_PDF = ""  # 路径由 config.local.yaml / 调用方给出


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default=DEFAULT_PDF)
    ap.add_argument("--start", type=int, default=84, help="起始 PDF 页（含）")
    ap.add_argument("--end", type=int, default=1864, help="结束 PDF 页（含）")
    ap.add_argument("--limit", type=int, default=0, help="只跑这么多页")
    ap.add_argument("--offset", type=int, default=83, help="印刷页 = PDF 页 - offset")
    ap.add_argument("--out", default="", help="输出目录（必填；原硬编码路径已移除）")
    ap.add_argument("--crop", choices=("cell", "box"), default="cell",
                    help="cell=按字格切固定正方形（推荐）；box=按墨迹框外扩成正方形")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)
    end = min(args.end, len(doc))
    pages = list(range(args.start, end + 1))
    if args.limit:
        pages = pages[: args.limit]

    os.makedirs(args.out, exist_ok=True)
    idx_path = os.path.join(args.out, "index.ndjson")
    done = 0
    t0 = time.time()
    with open(idx_path, "w", encoding="utf-8", newline="\n") as idx:
        for i, pno in enumerate(pages, 1):
            printed = pno - args.offset
            pdir = os.path.join(args.out, f"{printed:04d}")
            if os.path.isdir(pdir) and os.listdir(pdir) and not args.overwrite:
                continue
            os.makedirs(pdir, exist_ok=True)
            try:
                ink = load_ink(doc, pno)
            except Exception as e:  # 空白页 / 没有嵌图
                print(f"[skip] p{pno}: {e}", file=sys.stderr)
                continue
            heads = find_heads(ink)
            side = page_side(heads)
            for j, h in enumerate(heads, 1):
                img = head_crop(ink, h, side=side)
                hb = trim_box(ink, h)
                name = f"{printed:04d}_{h['band'] + 1}_{j:02d}.png"
                cv2.imwrite(os.path.join(pdir, name), img, [cv2.IMWRITE_PNG_BILEVEL, 1])
                idx.write(json.dumps(dict(
                    file=f"{printed:04d}/{name}", printed_page=printed, pdf_page=pno,
                    band=h["band"] + 1, seq=j, side=side,
                    x=hb["x"], y=hb["y"], w=hb["w"], h=hb["h"],
                ), ensure_ascii=False) + "\n")
                done += 1
            if i % 20 == 0 or i == len(pages):
                el = time.time() - t0
                print(f"[{i}/{len(pages)}] p{pno} 共 {done} 张  用时 {el:.0f}s  "
                      f"预计剩余 {el / i * (len(pages) - i):.0f}s", flush=True)
    print(f"完成：{done} 张 -> {args.out}")


if __name__ == "__main__":
    main()
