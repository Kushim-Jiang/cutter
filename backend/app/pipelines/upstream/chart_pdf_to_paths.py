#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unicode 代码表 PDF → 字形轮廓文本（`<字>_<变体>` + SVG path，一行一形）。

代码表每一格 = 一个码位，格里排着若干「来源字形」：代表字形（第一列）以及
H/T/J/K/P/V… 各来源的字形，每个字形下方用灰色小字标出它在来源里的编号
（如 `G0-4141`、`HB1-AB47`、`KP0-D7DC`）。这些字形不是图片，而是内嵌字体排出来的
文字，所以可以按 `texttrace` 给出的 gid 从内嵌字体程序里取出真正的轮廓。

输出格式（与 17.0.0.txt 相同）：

    一_G<TAB>M 1.15625 -8.703125 L 16.40625 -8.703125 ... Z

* 坐标系：em = 21 单位、基线 y = 0、**y 轴向下**（字形在上方，故 y 为负）
* 数值按 1/64 量化
* 变体字母取自字形下方来源编号的前缀（`KP0-…`→P、`SAT-…`→S、`UTC-…`→U…）；
  图表没标来源的（部首表、笔画表）记为 Q，当作代表字形

用法：

    python tools/chart_pdf_to_paths.py --pdf-dir <charts> --out temp/18.0.0.txt
    python tools/chart_pdf_to_paths.py --pdf-dir <charts> --dump-cells cells.tsv

依赖：pymupdf、fonttools
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

import pymupdf
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont

# ── 配置 ───────────────────────────────────────────────────────────────

EM = 21.0        # 1em 在输出坐标里的长度（沿用 17.0.0.txt）
GRID = 64.0      # 坐标量化：1/64

# 汉字相关区段（部首、笔画、扩展、兼容、小篆…）
TARGET_RANGES = [
    (0x2E80, 0x2FDF),    # CJK 部首补充 + 康熙部首
    (0x31C0, 0x31EF),    # CJK 笔画
    (0x3400, 0x4DBF),    # 扩展 A
    (0x4E00, 0x9FFF),    # 基本区
    (0xF900, 0xFAFF),    # 兼容汉字
    (0x2F800, 0x2FA1F),  # 兼容汉字补充
    (0x20000, 0x3FFFF),  # 扩展 B…J、小篆
]

# 版面上的「非字形」字体：标题、码位、名称、来源编号、注解、项目符号
NON_GLYPH_FONTS = (
    "MyriadPro", "Arial", "UnihanInfo", "UCSMyriadPro", "UCSSTIX",
    "TimesNewRoman", "Helvetica", "Courier",
    "Uni2000",  # Uni2000Generalpunctuation：注释行的「•」「~」「≡」等标点
)

# 小于这个字号的是格子里的部件注解（6pt），不是字形
MIN_GLYPH_SIZE = 8.0

HEX_RE = re.compile(r"^[0-9A-F]{4,6}$")
REF_RE = re.compile(r"^([A-Za-z]{1,5})[-\d]")

# 码位标签用的是 Arial / ArialNarrow；UCSMyriadPro 那类是「≡ 4E00 FE00」这种
# 等价字注释，编号长得也像码位，必须排除
LABEL_FONT_PREFIXES = ("Arial",)

# 来源编号前缀 → 变体字母（IRG 源 + 各字典/字书缩写的归并）
PREFIX_LETTER = {
    "G": "G", "GE": "G",
    "H": "H", "HB": "H",
    "T": "T",
    "J": "J", "JA": "J", "JK": "J", "JMJ": "J",
    "K": "K", "KC": "K", "KH": "K",
    "KP": "P",
    "V": "V", "VN": "V",
    "SAT": "S",
    "UTC": "U",
    "UK": "B",
    "MD": "M",
}


def in_target(cp: int) -> bool:
    return any(a <= cp <= b for a, b in TARGET_RANGES)


def is_non_glyph_font(font: str) -> bool:
    return any(font.startswith(p) for p in NON_GLYPH_FONTS)


def variant_letter(prefix: str) -> str:
    """来源编号前缀 → 变体字母；没有编号的算 Q（代表字形）。"""
    if not prefix:
        return "Q"
    p = prefix.upper()
    for n in (5, 4, 3, 2, 1):
        if p[:n] in PREFIX_LETTER:
            return PREFIX_LETTER[p[:n]]
    return p[0]


# ── 轮廓 → SVG path ────────────────────────────────────────────────────


class _PathPen(BasePen):
    """字体轮廓 → 17.0.0.txt 风格的 path（单位=pt、y 向下、1/64 量化）。

    缩放用**页面上的字号**（size/upem），不是固定 21：图表给不同来源字体排了
    不同字号（多数 21pt，KP/V 系 20pt、小篆 19/22pt），按字号换算才能保住图表里
    各来源之间的实际大小关系；整幅图的标准 em 就是 21pt。
    """

    def __init__(self, glyph_set, upem: int, size: float):
        super().__init__(glyph_set)
        self.upem = upem
        self.size = size
        self.parts: list[str] = []

    def _v(self, value: float) -> str:
        q = round(value / self.upem * self.size * GRID) / GRID
        if q == int(q):
            return str(int(q))
        return f"{q:.6f}".rstrip("0").rstrip(".")

    def _pt(self, p) -> str:
        return f"{self._v(p[0])} {self._v(-p[1])}"

    def _moveTo(self, p):
        self.parts.append("M " + self._pt(p))

    def _lineTo(self, p):
        self.parts.append("L " + self._pt(p))

    def _curveToOne(self, p1, p2, p3):
        self.parts.append("C " + " ".join((self._pt(p1), self._pt(p2), self._pt(p3))))

    def _qCurveToOne(self, p1, p2):
        # 二次贝塞尔 → 三次：控制点各取 2/3
        p0 = self._getCurrentPoint()
        c1 = (p0[0] + 2.0 / 3.0 * (p1[0] - p0[0]), p0[1] + 2.0 / 3.0 * (p1[1] - p0[1]))
        c2 = (p2[0] + 2.0 / 3.0 * (p1[0] - p2[0]), p2[1] + 2.0 / 3.0 * (p1[1] - p2[1]))
        self.parts.append("C " + " ".join((self._pt(c1), self._pt(c2), self._pt(p2))))

    def _closePath(self):
        self.parts.append("Z")

    def _endPath(self):
        self.parts.append("Z")

    def path(self) -> str:
        return " ".join(self.parts)


class ChartPdf:
    """一份代码表 PDF + 它内嵌字体的轮廓缓存。"""

    def __init__(self, path: Path):
        self.path = path
        self.doc = pymupdf.open(path)
        self._fonts: dict[int, tuple | None] = {}
        self._page_fonts: dict[int, dict[str, int]] = {}

    def close(self) -> None:
        self.doc.close()

    def font_xref(self, page, font_name: str) -> int | None:
        key = page.number
        mapping = self._page_fonts.get(key)
        if mapping is None:
            mapping = {}
            for f in page.get_fonts(full=True):
                xref, basefont = f[0], f[3]
                base = basefont.split("+", 1)[-1]
                mapping.setdefault(base, xref)
                # MuPDF 的 texttrace 只给前 25 个字符，长名字会被截断
                mapping.setdefault(base[:25], xref)
            self._page_fonts[key] = mapping
        hit = mapping.get(font_name)
        if hit is not None:
            return hit
        # 兜底：按前缀找唯一匹配（截断名 → 全名）
        cands = [name for name in mapping if name.startswith(font_name)]
        if len(cands) == 1:
            return mapping[cands[0]]
        if cands:
            return mapping[min(cands, key=len)]
        return None

    def _font(self, xref: int):
        if xref in self._fonts:
            return self._fonts[xref]
        entry = None
        try:
            _name, _ext, _type, buf = self.doc.extract_font(xref)
            if buf:
                font = TTFont(io.BytesIO(buf), fontNumber=0, lazy=True)
                entry = (font, font.getGlyphSet(), font["head"].unitsPerEm, font.getGlyphOrder())
        except Exception:  # noqa: BLE001 - 个别坏字体不该拖垮整批
            entry = None
        self._fonts[xref] = entry
        return entry

    def outline(self, xref: int, gid: int, size: float) -> str | None:
        entry = self._font(xref)
        if not entry:
            return None
        _font, glyph_set, upem, order = entry
        if gid >= len(order):
            return None
        pen = _PathPen(glyph_set, upem, size)
        try:
            glyph_set[order[gid]].draw(pen)
        except Exception:  # noqa: BLE001
            return None
        return pen.path() or None


# ── 版面解析 ───────────────────────────────────────────────────────────


def _span_text(span) -> str:
    return "".join(chr(c[0]) for c in span["chars"])


def parse_page(chart: ChartPdf, page):
    """解析一页，返回 [(码位, [(前缀, 字号, xref, gid, 顺序)])]。"""
    labels = []   # (x0, y0, x1, y1, cp)
    refs = []     # (x0, y0, x1, y1, text)
    glyphs = []   # (x0, y0, x1, y1, size, xref, gid, order)
    anchors = []  # 「≡ / ~ 4E00 金」这种注释行的锚点 (x0, y0)
    order = 0

    for sp in page.get_texttrace():
        if sp["wmode"]:
            continue
        text = _span_text(sp).strip()
        font = sp["font"]
        size = sp["size"]
        x0, y0, x1, y1 = sp["bbox"]
        if font.startswith("UCSSTIX") or font.startswith("MyriadPro-Light") or font.startswith("UCSMyriadProLight"):
            # 等价字注释（`≡ 81ED 臭` / `~ 81ED FE00`）：右边那个 10pt 小字是**别的字**的插图
            anchors.append((x0, y0))
            continue
        if font == "UnihanInfo":
            if REF_RE.match(text):
                refs.append((x0, y0, x1, y1, text))
            continue
        if HEX_RE.match(text) and 8.0 <= size <= 12.0 and font.startswith(LABEL_FONT_PREFIXES):
            labels.append((x0, y0, x1, y1, int(text, 16)))
            continue
        if is_non_glyph_font(font) or size < MIN_GLYPH_SIZE:
            continue
        xref = chart.font_xref(page, font)
        if xref is None:
            continue
        # 一行可能排着很多字形（部首表整行就是一段文字），逐个字符取自己的框
        for cp, gid, _origin, gbbox in sp["chars"]:
            # 汉字码位最低是 U+2E80（部首补充）；比它小的只可能是标点/字母/空格，
            # 出现在版面上就是注释符号，不是字形
            if cp < 0x2E80 and not in_target(cp):
                continue
            order += 1
            gx0, gy0, gx1, gy1 = gbbox
            known = cp if in_target(cp) else None
            glyphs.append((gx0, gy0, gx1, gy1, size, xref, gid, order, known))

    # 剔掉等价字注释里的插图：同一行（y 差 ≤5）、在 `≡`/`~` 右边 5~40pt 的那个
    if anchors:
        def is_annotation_art(gx0: float, gy0: float) -> bool:
            for ax, ay in anchors:
                if abs(gy0 - ay) <= 5.0 and 5.0 <= gx0 - ax <= 40.0:
                    return True
            return False

        glyphs = [g for g in glyphs if not is_annotation_art(g[0], g[1])]

    if not glyphs:
        return []

    # 版式是「多列 × 每列若干行」，同一格里的字形可能折到第二行；不同列的行高
    # 也不齐。所以先按 x 分列，再把每个字形归到「同列里、在它上方最近的那个
    # 码位标签」。没有码位标签的版式（康熙部首总表那种）就用字形自身的码位。
    cells: dict[int, list[tuple[float, int]]] = {}   # 列序号 → [(标签 y, 码位)]
    col_ranges: list[tuple[float, float]] = []
    if labels:
        labels = [lab for lab in labels if 60.0 <= lab[1] <= page.rect.height - 40.0]
        labels.sort(key=lambda lab: lab[0])
        columns: list[list[tuple]] = []
        for lab in labels:
            if columns and abs(columns[-1][0][0] - lab[0]) <= 6.0:
                columns[-1].append(lab)
            else:
                columns.append([lab])
        for ci, col in enumerate(columns):
            x_start = min(lab[0] for lab in col) - 3.0
            x_end = min(lab[0] for lab in columns[ci + 1]) - 3.0 if ci + 1 < len(columns) else page.rect.width
            col_ranges.append((x_start, x_end))
            cells[ci] = sorted((lab[1], lab[4]) for lab in col)

    def cell_of(gx0: float, gy0: float) -> int | None:
        for ci, (x_start, x_end) in enumerate(col_ranges):
            if x_start <= gx0 < x_end:
                best = None
                for lab_y, cp in cells.get(ci, ()):
                    if lab_y <= gy0 + 6.0:
                        best = cp
                    else:
                        break
                return best
        return None

    out = []
    for gx0, gy0, gx1, gy1, gsize, gxref, ggid, gorder, known in glyphs:
        cp = cell_of(gx0, gy0)
        if cp is None:
            cp = known
        if cp is None or not in_target(cp):
            continue
        prefix = ""
        best_dx = 99.0
        for rx0, ry0, rx1, ry1, rtext in refs:
            dy = ry0 - gy1
            if not (-3.0 <= dy <= 9.0):
                continue
            dx = abs(rx0 - gx0)
            if dx > 16.0 or dx >= best_dx:
                continue
            best_dx = dx
            m = REF_RE.match(rtext)
            prefix = m.group(1) if m else ""
        out.append((cp, [(prefix, gsize, gxref, ggid, gorder)]))

    return out


# ── 主流程 ─────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", required=True, help="存放 U*.pdf 的目录")
    ap.add_argument("--out", default="", help="输出 txt（不给则只做统计）")
    ap.add_argument("--dump-cells", default="", help="码位 → 来源前缀序列，写到 tsv 供校对")
    ap.add_argument("--limit-pages", type=int, default=0, help="每份 PDF 只读前 N 页（调试）")
    args = ap.parse_args()

    pdf_dir = Path(args.pdf_dir)
    pdfs = sorted(pdf_dir.glob("U*.pdf"))
    if not pdfs:
        sys.exit(f"{pdf_dir} 里没有 U*.pdf")

    charts: list[ChartPdf] = []
    best: dict[tuple[int, str], tuple] = {}   # (cp, letter) → (size, chart, xref, gid, order)
    prefixes_seen: dict[int, list[str]] = {}

    for pdf in pdfs:
        chart = ChartPdf(pdf)
        charts.append(chart)
        idx = len(charts) - 1
        pages = chart.doc.page_count
        if args.limit_pages:
            pages = min(pages, args.limit_pages)
        print(f"  {pdf.name}: {pages} 页", file=sys.stderr)
        for pno in range(pages):
            for cp, picks in parse_page(chart, chart.doc[pno]):
                seen = prefixes_seen.setdefault(cp, [])
                for prefix, size, xref, gid, order in picks:
                    if prefix and prefix not in seen:
                        seen.append(prefix)
                    letter = variant_letter(prefix)
                    cur = best.get((cp, letter))
                    if cur is None or size > cur[0] + 0.01:
                        best[(cp, letter)] = (size, idx, xref, gid, order)

    letters: dict[int, set[str]] = {}
    for cp, letter in best:
        letters.setdefault(cp, set()).add(letter)
    print(f"码位 {len(letters)}，字形 {len(best)}", file=sys.stderr)

    if args.dump_cells:
        with open(args.dump_cells, "w", encoding="utf-8", newline="\n") as fh:
            for cp in sorted(prefixes_seen):
                fh.write(f"{cp}\t{','.join(prefixes_seen[cp])}\n")
        print(f"前缀表 → {args.dump_cells}", file=sys.stderr)

    if not args.out:
        for chart in charts:
            chart.close()
        return

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    missing = 0
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        for cp in sorted(letters):
            # 同一字里按版面上的先后（先左右、再上下）排
            entries = sorted((best[(cp, lt)][4], lt) for lt in letters[cp])
            for _order, letter in entries:
                size, idx, xref, gid, _o = best[(cp, letter)]
                d = charts[idx].outline(xref, gid, size)
                if not d:
                    missing += 1
                    continue
                fh.write(f"{chr(cp)}_{letter}\t{d}\n")
                written += 1

    for chart in charts:
        chart.close()
    print(f"写出 {written} 个字形（缺轮廓 {missing}） → {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
