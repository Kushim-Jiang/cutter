#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把《汉字海》的印刷条目号 1–102434 编进字海 Z 组编号，并生成映射表。

Z 组原有分配（见 _zihai/pipeline/build_zihai_sprites.py）：`Z` + 18 进制桶 + 4 位位置号，
桶 A–H 装《中华字海》（18 个桶 × 10000 = 180000 个位置，已用满）。汉字海另开
`ZI`、`ZJ` 两桶，按**条目号**编码，桶内用 16 进制：

    ziid = "Z" + B36[18 + (e-1)//65536] + f"{(e-1) % 65536:04X}"

    e = 1       -> ZI0000
    e = 65536   -> ZIFFFF
    e = 65537   -> ZJ0000
    e = 102434  -> ZJ9021

反解：桶序 ≥ 18 即为汉字海，e = (idx-18)*65536 + int(hex,16) + 1。
字符串序 = 书序（先 I 后 J，桶内按条目号升序），ZK–ZZ 留空。

读 index.ndjson（字头坐标）+ fields.ndjson（OCR 字段），
写 ziid.ndjson：每框一行 {entry, ziid, page, seq, col, x, y, w, h, radical, strokes, code}

    python tools/hzh_ziid.py                         # 用 _zihai/hh 下的 index/fields
    python tools/hzh_ziid.py --data _zihai/hh --out _zihai/hh
"""
import argparse
import json
import os
import sys
from collections import Counter

B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
SLOT = 65536                    # 每桶 65536 个条目号
FIRST_BUCKET = 18               # A–H 是《中华字海》的 18 个桶
TOTAL = 102434                  # 汉字海条目总数（书末第 102434 号）


def encode(e):
    """条目号 → Z 组编号。"""
    if not 1 <= e <= TOTAL:
        raise ValueError(f"条目号越界：{e}")
    k = (e - 1) // SLOT
    return "Z" + B36[FIRST_BUCKET + k] + f"{(e - 1) % SLOT:04X}"


def decode(ziid):
    """Z 组编号 → 条目号；不是汉字海段就返回 None。"""
    if len(ziid) != 6 or ziid[0] != "Z":
        return None
    k = B36.find(ziid[1])
    if k < FIRST_BUCKET:
        return None
    return (k - FIRST_BUCKET) * SLOT + int(ziid[2:], 16) + 1


# ---------------------------------------------------------------- 读数据

def read_ndjson(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def order_rows(rows):
    """定一页之内的阅读顺序。

    页内可能有多条"横带"（换部首小节、换画数小节都会从左边栏重新起一条），
    单纯按 (栏, y) 排会乱。最靠得住的顺序其实是**书上的条目号**：几乎每条都有读数，
    所以按读数排；没读数、或读数与本栏中位数差太远（读成 1、11、1111 这种）的，
    按同栏上下邻居插进去。最后再用"每栏 y 必须递增"核一遍，不对就退回 (栏, 行)。
    """
    cand = sorted(r["obs"] for r in rows if r["obs"])
    med = None
    for _ in range(3):                           # 迭代中位数：把正文里的小数字读数剔出去
        if not cand:
            break
        med = cand[len(cand) // 2]
        keep = [o for o in cand if abs(o - med) <= 60]
        if not keep or len(keep) == len(cand):
            break
        cand = keep

    def good(r):
        o = r["obs"]
        return bool(o) and (med is None or abs(o - med) <= 60)

    sure = sorted((r for r in rows if good(r)), key=lambda r: r["obs"])
    unsure = [r for r in rows if not good(r)]

    def build(sure_list, unsure_list):
        order = list(sure_list)
        for r in sorted(unsure_list, key=lambda r: (r["col"], r["y"])):
            above = None
            for i, q in enumerate(order):
                if q["col"] == r["col"] and q["y"] < r["y"]:
                    above = i
            if above is None:                    # 本栏最上面：插在该栏第一个前面
                idx = next((i for i, q in enumerate(order) if q["col"] == r["col"]), len(order))
                order.insert(idx, r)
            else:
                order.insert(above + 1, r)
        return order

    order = build(sure, unsure)
    for _ in range(4):                           # 有读数排乱了：把那几条当误读，重来
        drop, prev_y = [], {}
        for r in order:
            py = prev_y.get(r["col"], -1)
            if r["y"] < py:
                drop.append(id(r))
            else:
                prev_y[r["col"]] = r["y"]
        if not drop:
            return order
        moved = [r for r in sure if id(r) in set(drop)]
        sure = [r for r in sure if id(r) not in set(drop)]
        if not moved:
            break
        order = build(sure, unsure + moved)
    ys_by_col = {}
    for r in order:
        ys_by_col.setdefault(r["col"], []).append(r["y"])
    if any(any(b < a for a, b in zip(ys, ys[1:])) for ys in ys_by_col.values()):
        return sorted(rows, key=lambda r: (r["col"], r["y"]))
    return order


def load_pages(data):
    """按 页 → 阅读顺序 整理字头框，并把 OCR 字段按 (page, seq) 挂上去。

    阅读顺序不是简单的"栏 → 行"：一页里如果换了部首小节，新版面会**重新从左边栏
    起一条新的横带**（例如 p489：上带是辵部，下带从左边栏接着排乙部）。所以先排
    (col, y)，再按横带切一次，最后按 (带, col, y) 排。
    """
    fields = {}
    for name in ("fields.ndjson", "fields_fix.ndjson"):     # fix 覆盖原结果
        p = os.path.join(data, name)
        if os.path.exists(p):
            fields.update({(r["page"], r["seq"]): r for r in read_ndjson(p)})
    nums = {}
    p = os.path.join(data, "fields_num.ndjson")            # 只补"原读数空着"的号码
    if os.path.exists(p):
        nums = {(r["page"], r["seq"]): r["entry"] for r in read_ndjson(p)}
    pages = {}
    for r in read_ndjson(os.path.join(data, "index.ndjson")):
        pages.setdefault(r["page"], []).append(r)
    for pno, rows in pages.items():
        rows.sort(key=lambda r: (r["col"], r["y"]))
        for r in rows:
            f = fields.get((pno, r["seq"]), {})
            r["obs"] = f.get("entry") or nums.get((pno, r["seq"]))
            r["radical"] = f.get("radical")
            r["strokes"] = f.get("strokes")
            r["code"] = f.get("code")
        rows = order_rows(rows)
        pages[pno] = rows
        for pos, r in enumerate(rows):
            r["pos"] = pos
    return pages


# ---------------------------------------------------------------- 排号

def fit_page(obs, chain):
    """拟合出一页从 0 到 n-1 的条目号，尽量贴合书上的读数。

    页内条目号连续，唯一的不确定是"哪里漏了框"。从左往右走：读数等于当前预测就确认；
    读数比预测大 k（1–6）说明前面漏了 k 个条目，跳过去；读数变小/离谱就当误读忽略。

    起始号的候选：上页末号 + 1（链条），以及前几条读数各自推出来的起点。链条平时最靠
    得住（页首号基本就是上页末号 + 1），所以给它一点加权，读数只在明显更吻合时才推翻它。
    """
    n = len(obs)
    cands = [(chain, 1.5)] if chain else []        # (起点, 加权)
    for i, o in enumerate(obs[:10]):
        if o:
            cands.append((o - i, 0.0))
    best = None
    for s, bias in cands:
        if not s or s < 1:
            continue
        score, est, cur = bias, [], s
        for i in range(n):
            o = obs[i]
            if o and o == cur:
                score += 1
            elif o and 0 < o - cur <= 6:
                score += 1
                cur = o                        # 前面漏了条目
            elif o and 0 < cur - o <= 4:
                score += 0.5
                cur = o                        # 多框/重复框 → 重新对齐
            elif o:
                score -= 1                     # 误读：忽略这条读数
            est.append(cur)
            cur += 1
        if best is None or score > best[0]:
            best = (score, est)
    return best[1] if best else [None] * n


def assign_pages(pages):
    """每页串号。

    第一遍按读数拟合（能跨过真漏切）。第二遍看**条目数**：一页的框数如果正好等于
    "本页首号 → 下页首号"的跨度，那这页就没有漏号，直接连续排 —— 读数跳号一律当误读。
    （p1788 就是这样：读数把 61995 读成 62001，按读数排会凭空造出 6 个"缺号"。）
    每页的首号始终来自它自己的读数，所以第二遍只是页内重排，不会让误差往后累积。
    """
    order = sorted(pages)
    first, stats, notes = {}, Counter(), []
    chain = None
    for pno in order:
        rows = pages[pno]
        obs = [r["obs"] if r["obs"] and r["obs"] > 0 else None for r in rows]
        est = fit_page(obs, chain)
        first[pno] = est
        if chain and est and est[0] != chain:
            notes.append((pno, f"首号 {est[0]}，上页链条 {chain}（差 {est[0] - chain}）"))
        chain = est[-1] + 1 if est and est[-1] else None

    out = {}
    for i, pno in enumerate(order):
        rows = pages[pno]
        obs = [r["obs"] if r["obs"] and r["obs"] > 0 else None for r in rows]
        est, s = first[pno], first[pno][0]
        nxt = first[order[i + 1]][0] if i + 1 < len(order) else s + len(rows)
        if nxt - s == len(rows):
            est = list(range(s, s + len(rows)))
            stats["contig"] += 1
        elif nxt - s < len(rows):
            # 框比号码还多：没读数的那几个多半是页眉/装饰误切，剔掉再连续排
            keep = [j for j, o in enumerate(obs) if o is not None] or list(range(len(rows)))
            if len(keep) == nxt - s:
                est = [None] * len(rows)
                for k, j in enumerate(keep):
                    est[j] = s + k
                stats["contig"] += 1
            else:
                stats["holes"] += 1
        else:
            stats["holes"] += 1
        for o, e in zip(obs, est):
            if o and e:
                stats["agree" if o == e else "miss"] += 1
        out[pno] = (est, "")
    return out, stats, notes


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--data", default=os.path.join(here, "_zihai", "hh"))
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    out = args.out or args.data

    pages = load_pages(args.data)
    if not pages:
        sys.exit(f"{args.data} 里没有 index.ndjson")
    assign_all, stats, notes = assign_pages(pages)

    rows_out, seen, dup = [], Counter(), 0
    for pno in sorted(pages):
        rows = pages[pno]
        assign, note = assign_all[pno]
        if note:
            notes.append((pno, note))
        for r, e in zip(rows, assign):
            if e is None or not 1 <= e <= TOTAL:
                continue
            if seen[e]:
                dup += 1                    # 同一个号落到两个框上，留书序在前的那一个
                continue
            seen[e] += 1
            rows_out.append(dict(entry=e, ziid=encode(e), page=pno, seq=r["seq"],
                                 col=r["col"], x=r["x"], y=r["y"], w=r["w"], h=r["h"],
                                 radical=r["radical"], strokes=r["strokes"], code=r["code"]))

    rows_out.sort(key=lambda r: r["entry"])
    import hzh_radical as R                     # 部首改成康熙部首口径
    rad = R.fix_radicals(rows_out)
    rad_src = Counter(r.get("radical_src") for r in rows_out)
    for r in rows_out:                          # 中间量不必落盘
        r.pop("radical_dict", None)
        r.pop("dict_strokes", None)
        r.pop("radical_ocr", None)              # 只留一个 radical，值一律是康熙部首
        r.pop("radical_src", None)
    os.makedirs(out, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "ziid.ndjson")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows_out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 分片：一个桶一个文件（ZI、ZJ），行按 ziid 升序
    shards = Counter()
    by_bucket = {}
    for r in rows_out:
        by_bucket.setdefault(r["ziid"][:2], []).append(r)
    for key, rows in sorted(by_bucket.items()):
        with open(os.path.join(out, key + ".ndjson"), "w", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        shards[key] = len(rows)

    miss = [e for e in range(1, TOTAL + 1) if not seen[e]]
    where = {r["entry"]: r for r in rows_out}
    with open(os.path.join(out, "missing_entries.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# 《汉字海》1–%d 里没落框的条目号：号<TAB>前一条目@页<TAB>后一条目@页\n" % TOTAL)
        for e in miss:
            prev, nxt = where.get(e - 1), where.get(e + 1)
            fh.write("%d\t%s\t%s\n" % (
                e,
                "p%d" % prev["page"] if prev else "-",
                "p%d" % nxt["page"] if nxt else "-"))
    with open(os.path.join(out, "page_counts.ndjson"), "w", encoding="utf-8", newline="\n") as fh:
        for pno in sorted(pages):
            n_box = len(pages[pno])
            n_ent = sum(1 for e in assign_all[pno][0] if e)
            fh.write(json.dumps(dict(page=pno, boxes=n_box, entries=n_ent),
                                ensure_ascii=False) + "\n")
    buckets = Counter(r["ziid"][:2] for r in rows_out)
    print(f"页 {len(pages)}  框 {sum(len(v[0]) for v in assign_all.values())}  写出 {len(rows_out)}  重复丢弃 {dup}")
    print(f"条目号 1–{TOTAL}：有 {len(seen)}  缺 {len(miss)}")
    n_obs = stats["agree"] + stats["miss"]
    if n_obs:
        print(f"条目号：拟合结果与读书一致 {stats['agree']}（{100.0 * stats['agree'] / n_obs:.1f}%），"
              f"不一致 {stats['miss']}")
    if dup:
        print(f"重复丢弃 {dup} 行")
    n_rad = sum(1 for r in rows_out if r["radical"])
    print(f"部首（康熙口径）：字典定 {rad_src.get('dict', 0)}，读数定 {rad_src.get('ocr', 0)}，"
          f"邻居补 {rad_src.get('near', 0)}，最终 {n_rad}/{len(rows_out)} = "
          f"{100.0 * n_rad / max(1, len(rows_out)):.1f}%")
    print("桶：" + "  ".join(f"{k}={v}" for k, v in sorted(buckets.items())))
    if miss:
        rng, s = [], miss[0]
        for a, b in zip(miss, miss[1:] + [None]):
            if b != (a + 1 if a is not None else None):
                rng.append(f"{s}" if s == a else f"{s}-{a}")
                s = b
        print("缺号区段：" + " ".join(str(x) for x in rng[:20]) + (" …" if len(rng) > 20 else ""))
    if notes:
        print(f"注意 {len(notes)} 处，前 10：")
        for pno, note in notes[:10]:
            print(f"  p{pno}: {note}")
    print("->", path)


if __name__ == "__main__":
    main()
