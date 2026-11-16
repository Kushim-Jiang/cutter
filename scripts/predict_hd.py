"""批量生成《汉语大字典》第一册的字头预测框（版面法 hd_heads）。

输出与上游 `hd1-heads.ndjson` 同格式，可被 `HdPipeline` 当 seed 读：

    {"pdf": <0 基页号>, "box": [x, y, w, h], "col": <0|1>, "src": "pred"}

可断点续跑：已经在输出文件里的页会跳过（算法改进后要重出某一段就加 `--replace`）。

用法：
    uv run python scripts/predict_hd.py --from 498 --to 5106
    uv run python scripts/predict_hd.py --from 1498 --to 5106 --replace
    uv run python scripts/predict_hd.py --from 498 --to 1200 --out <path>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.pipelines.registry import get_pipeline  # noqa: E402


def read_done(path: Path) -> set[int]:
    done: set[int] = set()
    if not path.is_file():
        return done
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(int(json.loads(line)["pdf"]) + 1)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
    return done


def drop_range(path: Path, start: int, end: int) -> int:
    """把输出文件里 `start..end`（1 基 PDF 页）的旧预测删掉，其余原样保留。"""
    if not path.is_file():
        return 0
    kept: list[str] = []
    dropped = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            raw = line.strip()
            if not raw:
                continue
            try:
                pno = int(json.loads(raw)["pdf"]) + 1
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                kept.append(raw)
                continue
            if start <= pno <= end:
                dropped += 1
            else:
                kept.append(raw)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8", newline="\n")
    tmp.replace(path)
    return dropped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, required=True, help="起始 PDF 页（1 基，含）")
    ap.add_argument("--to", dest="end", type=int, required=True, help="结束 PDF 页（1 基，含）")
    ap.add_argument("--out", type=Path, default=None, help="输出文件（默认 <seed_dir>/hd1-pred.ndjson）")
    ap.add_argument(
        "--replace",
        action="store_true",
        help="先删掉这个区间里的旧预测再重跑（算法改进后重出某一段用）",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    pipe = get_pipeline("hd")
    out = args.out or pipe.pred_file()
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.replace:
        dropped = drop_range(out, args.start, args.end)
        print(f"删除 {out} 里 {args.start}-{args.end} 的旧预测 {dropped} 行", flush=True)
    done = read_done(out)
    todo = [p for p in range(args.start, args.end + 1) if p not in done]
    if not todo:
        print(f"没有要处理的页（{out} 已覆盖 {args.start}-{args.end}）")
        return 0

    print(f"输出 {out}；待处理 {len(todo)} 页（{todo[0]}..{todo[-1]}）", flush=True)
    t0 = time.time()
    with out.open("a", encoding="utf-8", newline="\n") as fh:
        for n, pno in enumerate(todo, 1):
            t1 = time.time()
            try:
                result = pipe.detect(f"1:{pno}", {"method": "layout"})
            except Exception as exc:  # 单页失败不影响整体
                print(f"  p{pno} 失败：{type(exc).__name__}: {exc}", flush=True)
                continue
            for box in result.boxes:
                fh.write(
                    json.dumps(
                        {"pdf": pno - 1, "box": [box.x, box.y, box.w, box.h], "col": 0, "src": "pred"},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            fh.flush()
            if not args.quiet and (n % 20 == 0 or n == len(todo)):
                rate = (time.time() - t0) / n
                left = rate * (len(todo) - n)
                print(
                    f"  {n}/{len(todo)}  p{pno}  {len(result.boxes)} 框  "
                    f"{time.time() - t1:.1f}s/页  预计剩余 {left / 60:.0f} 分钟",
                    flush=True,
                )
    print(f"完成：{out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
