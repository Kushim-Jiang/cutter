# 书源（切图管线）说明

## 上游算法地图

`backend/app/pipelines/upstream/` 里的文件是从 `yedict-data/tools/` **逐字复制**的，
不改逻辑，只由 `pipelines/*.py` 调用。

| 上游文件 | 用途 | 被谁调用 |
| --- | --- | --- |
| `zihai_grid.py` | 《中华字海》版面网格：三栏 → 行距/相位 → 栏左缘字头；`head_crop` 裁切 | `pipelines/zihai.py` |
| `zihai_crop.py` | 整本《中华字海》逐页切图 CLI（保留备查） | — |
| `hzh_crop.py` | 《汉字海》字头框定位（栏左缘大字 + 右侧序号） | `pipelines/hzh.py` |
| `hzh_recut.py` / `hzh_fields.py` / `hzh_ziid.py` / `hzh_radical.py` / `hzh_review.py` | 补切、属性行 OCR、Z 编号、部首口径、复核前端 | 备查 |
| `kx_locate.py` | 康熙内府本字头大字定位 | `pipelines/kx.py` |
| `kx_pagemap.py` / `build_kx_sprites.py` | 页序映射、按位置出雪碧图 | 备查（雪碧图生产） |
| `add_sprites.py` / `build_u_sprites.py` / `build_t_sprites.py` / `chart_pdf_to_paths.py` | 字形入雪碧图、字体/代码表取轮廓 | 备查 |

> 上游这些脚本原本有 argparse 的 `main()` 与硬编码路径；作为模块导入时不会执行，
> 适配层只调用纯函数并显式传参数。

## 各书源

### 中华字海 `zihai`

* 设置：`settings.books.zihai`
  * 单卷：`pdf` + `first_page` / `last_page` / `offset`
  * 多卷：`volumes: {"1": {pdf, offset, first_page, last_page, box_margin, title}, "2": {...}}`
* 页号：`<卷>:<PDF页>`，例如 `1:84`；只配 `pdf` 时卷号固定为 `1`。
* 印刷页 = PDF 页 − offset（卷 1 默认 83，卷 2 默认 79）。
* 检测参数：
  * `crop_mode`：`cell`（边长 2×行距的正方形，整页同尺寸）/ `glyph`（按墨迹外扩）
  * `margin`：`glyph` 时的外扩比例
  * `tighten` / `box_margin`：自动框按墨迹收紧（等价校对台「吸附墨迹」）
  * `flag_review`：给尺寸离群 / 带孤立墨点的框打「待查」标记
* 导出文件名：`<卷><印刷页:04d>_<序号:03d>.png`。

### 汉字海 `hzh`

* 设置：`pdf` + `first_page`（默认 452）/ `last_page`（2648）/ `offset`（451）。
* 页号：`PDF页`。
* `detect` 返回 `margins`（三条栏左缘）与 `suspects`（疑似漏切的属性行位置）。
* 导出参数：`pad`（外扩比例，0 = 严格按框）。

### 康熙字典（内府本）`kx`

* 设置：`book_dir` 指向 13 册 300dpi 扫描 PDF（文件名含 `康熙字典.NN`）。
* 页号：`<分册>:<页>`，分册取文件名里的两位数字，例如 `07:100`。
* 尺度：渲染 zoom 默认 300/72，与上游 `big_glyphs` 的像素阈值（`min_side=95` 等）一致。
* 检测参数：`thick_px` / `join_px` / `min_side` / `min_area` / `pad`。
* 位置号：`kx_pos_key(page, index)` = `K` + 页百位(0-Z) + 页后两位 + 位次两位（隐编号）。
* 阅读顺序：内府本竖排右起（先按 x 中心从右到左，再按 y）。

### 汉语大字典 `hd`

《汉语大字典》第一版（1986–1990，8 卷合订，6044 页，无文字层）与第二版（2010，5777 页，自带 OCR 文字层）。

两版版式相同：**双栏密排，字头比正文大得多，条目开头还排古文字形示例（也是大字）**。
形状上分不开，所以定位靠版面约束。

* 设置：`volumes`{1,2}，各自 `pdf` / `first_page` / `last_page` / `offset`。
  第一版 `297–5106`、`offset=296`（PDF 页 − 296 = 印刷页）；第二版 `1–5127`、`offset=0`。
* `dpi` 默认 **300**，与上游坐标表同一分辨率，seed 直接套用、不做缩放。
* 页号：`<册>:<1 基 PDF 页>`，例如 `2:501`。

算法全部移植自 `yedict-data/_up/handa/scripts/`：

| 上游 | 移植件 | 做什么 |
|---|---|---|
| `hd_heads.py` | `upstream/hd_heads.py` | 连通域取大字块 → 竖投影切两栏 → **每栏只留最左一列** |
| `hd_v2.py` | `upstream/hd_v2.py` | 文字层里 26–40pt 的单字 span → 分栏 → 最左列 → 收紧到墨迹 |
| `hd1-heads.ndjson` | `seed_boxes` | 第一版定稿字头框（52,053 条，300dpi） |
| `hd2.csv` | `_expect` | 第二版各页字头序列（用于过滤文字层里的古文字形） |

和上游的两处差异（都是必需的修正）：

1. **正文中位字高自适应**。上游写死 `40 ≤ 高 ≤ 100`（分析图分辨率），那是第一版
   正文约 106px 定的；第二版正文只有约 65px，落在范围外，字头被当成正文，
   于是一个框都检不出来。改为取全分布众数（正文总是出现次数最多的字块）。
2. **页脚忽略线**（`footer`，默认 0.9）。页码/藏书印这类大块常挤在栏内最左边，
   会把真正的字头挤出「最左一列」，导致整栏漏检。

检测参数：`method`（`layout` 默认 / `text` / `both`）、`min_ratio`（1.45）、
`max_side`（340）、`footer`（0.9）、`min_pt`/`max_pt`（26/40）、`use_index`。

页范围与进度：

- 每册 `first_page`/`last_page`（PDF 页，1 基）限定可访问范围，默认第一册 `297–5106`、
  第二册 `1–5127`。范围外的页直接返回 404（界面跳转也会提示本册开放范围），
  不会停在渲染不出来的封面/版权页上。
- `seed_counts_done: false`（默认）：第一版定稿是**自动检测的初稿**，有框可以当 seed，
  但不算「已校对完」，`progress` 从 0 开始，`下一未完成` 会从头走一遍。
  中华字海/汉字海/康熙的定稿来自已做完的人工校对，仍按完成计。

> 第二版文字层只给出少量字头（PP-OCRv3 对孤立大字准确率约 17%），
> 所以 `text` 只适合补充，默认走 `layout`。上游 `hd_v2.py` 也还没产出 `hd2-heads.ndjson`。

导出文件名：`H<册>-<4 位 PDF 页>_<序号>.png`。

## 新增一个书源

```python
# backend/app/pipelines/mydict.py
from .base import DetectResult, Pipeline, param
from ..core.box import Box

class MyPipeline(Pipeline):
    id = "mydict"
    name = "某某字典"
    description = "..."

    def __init__(self, settings): self.settings = settings
    def status(self): ...
    def params_spec(self): return [param("margin", "外扩", "number", 0.0)]
    def pages(self): ...
    def gray(self, page_id): ...          # 坐标空间 = 这张图
    def detect(self, page_id, params): return DetectResult(boxes=[...], meta={...})
```

然后在 `pipelines/registry.py` 的 `_CLASSES` 与 `ORDER` 里登记。

注意：

* `gray()` 的尺寸就是前端拿到的坐标空间，`meta.width/height` 必须与之一致。
* `detect` 返回的 `Box` 可带 `band/line/col/seq/pos/pitch/cw`，`order()` 与导出会用到。
* 导出裁剪默认「按框裁」，需要特殊裁法就覆写 `export_image`。

## 对齐表（跨来源逐行核对）

`backend/app/align.py` + `api/align.py` + `frontend/src/components/AlignTable.svelte`。

一列「字海1.0 字 / ziid」+ 两列切图（卷1 / 卷2），按**页内位置号**逐行对齐，
用来查三份来源有没有错位。算法移植自 `yedict-data/_zihai/table/build_table.py`，
但**不生成那份 36MB 自包含 HTML**：

* 网页按页现算（一次一页，几十行），切图直接走 `/crop`；
* 框数来源**本地存盘优先**，其次上游定稿 —— 所以编辑器里改完框，回表里看到的就是新结果，
  不用再跑上游那 2.5 分钟的切图条重建；
* `notes.json` 的三种标注（`miss` 某侧失收 / `into` 字海重复占位 / `extra` 某侧多切）照旧生效。

`GET /api/books/zihai/align?page=N` → 行 + 两卷页码 + 是否对齐；点任一切图跳到编辑器里那个框。

Z/P 系的 `ziid` 不含码位（占 28%），第一列只能靠雪碧图：`GET .../align/sprite?page=N&pos=K`
从 `sprites_z/zh10/sheets/*.png`（128×128 格，1.0 缺的用 zh11 补）裁一格。

> 目前只有中华字海有三方来源可交叉核对（字海1.0 / 卷1 / 卷2），其它书源返回 404。

## 手工对齐表（可编辑）

`backend/app/sheets.py` + `api/sheets.py` + `frontend/src/components/SheetView.svelte`。

对应旧 PySide6 桌面端被删掉的 `ui_table/table_view.py`：**一列图 + 一列文本**，
人工把图配到正确的字上，再导出 TSV。和图海那份只读核对表的区别是这里可以改。

* 建表：图片来自「当前页的切图」或「本地文件夹」（按文件名自然序：p1/p2/p10）；文本可以是粘贴的多行，
  也可以直接读一份 **TSV**（`tsv_path`，本工具导出的 `#/img/text/note`）：`img` 列按文件名（文件夹源）
  或 `<页>#<序号>`（页源）重新绑定，空着就是解绑。粘贴的多行也认 4 列 / `号\t文本` / 纯文本。
* 编辑：`Ctrl+I` 合并下一行、`Ctrl+U` 在光标处拆分、`Shift+↑/↓` 整行上下换、
  `↑图 / ↓图` 只对调图片、`解绑 / 配图`、`+行 / ✕`。
* 每次改动整表存盘（`work/sheets/<id>.json`，带 `rev`，别处改过会 409）。
* 导出 TSV：`#	img	text	note`；相对路径落在 `WORK_DIR` 下。

> 图片按需裁：`GET /api/sheets/{id}/image/{index}` 直接从页图裁（或读本地文件），
> 不为一张表生成一堆中间图。
