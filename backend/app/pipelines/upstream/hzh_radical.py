#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纠正《汉字海》每条的部首：改用**康熙 214 部首**口径，不再照抄属性行的 OCR 读数。

为什么这么改：

* 属性行里那个「X部」只有 45–58px 高，OCR 读数约 89% 准，错的时候会给出「系/廿/井/左/右」
  这类形近字，或者干脆认成正文里的字 —— 直接拿来当部首不能看。
* 字典 U 组里每个字本来就有 `radical`，**正好 214 种**，就是康熙部首（形如 艸/水/手/金/糸）；
  《汉字海》p223 的《部首表》也是这一套，每个部首后面跟着它的正体和异体写法。
* 所以：**有 U 码位的字，直接用字典的康熙部首**；没有码位的，用它左右邻居的部首补 ——
  书是按部首分节排的，邻居同节，补出来几乎总对。属性行的读数留在 `radical_ocr` 里备查。

    python tools/hzh_radical.py --check      # 读数与字典部首的一致率、最常见的错法
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter


def dict_radicals(root):
    """U 组字典 → {码位: (康熙部首, 总笔画)}。"""
    out = {}
    for f in glob.glob(os.path.join(root, "dict", "U*.ndjson")):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                o = json.loads(line)
                zi = o.get("ziid", "")
                if len(zi) == 6 and zi[0] == "U" and o.get("radical"):
                    try:
                        out[int(zi[1:], 16)] = (o["radical"], o.get("stroke_total"))
                    except ValueError:
                        continue
    return out


def code_of(r):
    c = r.get("code")
    if not c:
        return None
    try:
        return int(c, 16)
    except ValueError:
        return None


def learn_ocr_map(rows, kangxi):
    """学「属性行读数 → 康熙部首」的等价类。

    只对**不是**康熙部首本字的写法起作用（氵、亻、扌、廿、系、礻…）；本来就是标准部首的字
    （广、口、十、小、一…）原样保留 —— 否则 OCR 把 疒 读成 广 时会被"多数票"误折到
    另一边，把真的广部字也改错。
    """
    pair = {}
    for r in rows:
        o, k = r.get("radical_ocr"), r.get("radical_dict")
        if o and k and o not in kangxi:
            pair.setdefault(o, Counter())[k] += 1
    out = {}
    for o, c in pair.items():
        k, n = c.most_common(1)[0]
        if n >= max(3, 0.6 * sum(c.values())):
            out[o] = k
    return out


def decide(r, fold, kangxi):
    """结合「码位查字典」与「属性行读数」定一条的康熙部首。"""
    d, dst = r.get("radical_dict"), r.get("dict_strokes")
    ocr = r.get("radical_ocr")
    cand = ocr if ocr in kangxi else fold.get(ocr)
    if d and cand == d:
        return d, "dict"
    if d and not cand:
        return d, "dict"
    if d and cand != d:
        st = r.get("strokes")
        if st and dst and abs(st - dst) <= 1:
            return d, "dict"          # 笔画对得上字典那个字 → 码位没读错，信字典
        return cand, "ocr"            # 否则信书上印的部首
    if cand:
        return cand, "ocr"
    return None, None


def fix_radicals(rows, root=None):
    """就地把 rows 的部首换成康熙部首；返回统计。

    rows 必须按书序（entry 升序）排好。属性行读数先存进 `radical_ocr` 再覆盖 `radical`。
    """
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dic = dict_radicals(root)
    kangxi = {v[0] for v in dic.values()}
    for r in rows:
        r["radical_ocr"] = r.get("radical")
        cp = code_of(r)
        d = dic.get(cp) if cp is not None else None
        r["radical_dict"] = d[0] if d else None
        r["dict_strokes"] = d[1] if d else None
    fold = learn_ocr_map(rows, kangxi)

    got = []
    for r in rows:
        rad, src = decide(r, fold, kangxi)
        r["radical"], r["radical_src"] = rad, src
        got.append(rad)

    filled = 0
    known = [i for i, rad in enumerate(got) if rad]
    for a, b in zip(known, known[1:]):
        if b - a <= 1:
            continue
        prev, nxt = got[a], got[b]
        for i in range(a + 1, b):
            if got[i]:
                continue
            rows[i]["radical"] = nxt if nxt != prev else prev
            rows[i]["radical_src"] = "near"
            got[i] = rows[i]["radical"]
            filled += 1
    if known:
        for i in list(range(known[0])) + list(range(known[-1] + 1, len(rows))):
            if got[i]:
                continue
            side = known[0] if i < known[0] else known[-1]
            rows[i]["radical"] = got[side]
            rows[i]["radical_src"] = "near"
            got[i] = rows[i]["radical"]
            filled += 1
    return dict(dict=dic, fill=fold, filled=filled)


def assemble(root, data):
    """按书序拿到全部条目（含页码、码位、读数）。"""
    sys.path.insert(0, os.path.join(root, "tools"))
    import hzh_ziid as Z
    pages = Z.load_pages(data)
    assign_all, _stats, _notes = Z.assign_pages(pages)
    rows, seen = [], set()
    for pno in sorted(pages):
        for r, e in zip(pages[pno], assign_all[pno][0]):
            if e is None or not 1 <= e <= Z.TOTAL or e in seen:
                continue
            seen.add(e)
            rows.append(dict(page=pno, seq=r["seq"], col=r["col"], x=r["x"], y=r["y"],
                             w=r["w"], h=r["h"], entry=e, code=r["code"],
                             radical=r["radical"], strokes=r["strokes"]))
    rows.sort(key=lambda r: r["entry"])
    return rows


def check(root, data):
    rows = assemble(root, data)
    st = fix_radicals(rows, root)
    dic = st["dict"]
    n = len(rows)
    have = sum(1 for r in rows if r["radical_src"] == "dict")
    print(f"条目 {n}  字典定 {have}（{100.0 * have / n:.1f}%）"
          f"  读数定 {sum(1 for r in rows if r['radical_src'] == 'ocr')}"
          f"  邻居补 {st['filled']}")
    both = [r for r in rows if r["radical_ocr"] and r["radical"]]
    agree = sum(1 for r in both if r["radical_ocr"] == r["radical"])
    print(f"属性行读数与康熙部首一致 {agree}/{len(both)} = {100.0 * agree / max(1, len(both)):.1f}%")
    bad = Counter((r["radical_ocr"], r["radical"]) for r in both if r["radical_ocr"] != r["radical"])
    print("最常见的读数偏差（读数 → 最终）：")
    for (o, k), c in bad.most_common(20):
        print(f"   {o} → {k}  {c}")
    print(f"仍无部首 {sum(1 for r in rows if not r['radical'])}")
    print("康熙部首分布前 20：", Counter(r["radical"] for r in rows).most_common(20))
    print("部首来源：", Counter(r["radical_src"] for r in rows).most_common())


def main():
    ap = argparse.ArgumentParser()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--data", default=os.path.join(root, "_zihai", "hh"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        check(root, args.data)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
