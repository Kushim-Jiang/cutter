# 架构

## 分层

```
前端 Svelte 5  ──HTTP/JSON──▶  FastAPI
                                │
                                ├─ api/     路由与参数校验（books / align / sheets / export / jobs / settings）
                                ├─ pipelines/  书源：页列表、取图、检测、导出
                                │    └─ upstream/  yedict-data/tools 的算法原件（逐字复制）
                                ├─ core/    与书源无关的图像与几何工具
                                ├─ store.py 每页字框 sidecar + 编辑流水
                                └─ jobs.py  批量任务（线程池）
```

设计目标：**所有字典在界面上是同一套操作**（页 → 检测 → 校对 → 导出），
差异只落在 `Pipeline` 的 `pages / gray / ink / detect / order / export_image` 上。

## 后端

`Pipeline`（`backend/app/pipelines/base.py`）是书源接口：

| 方法 | 说明 |
| --- | --- |
| `status()` | 书源是否可用、页数、分卷 |
| `pages()` | 页列表：`{id, pdf_page, printed_page, label, volume?}` |
| `gray(page_id)` | 整页灰度图（坐标空间即此图） |
| `ink(page_id)` | 二值墨迹（默认 `gray < 128`，有 1bit 原图的覆写） |
| `detect(page_id, params)` | 自动框，返回 `DetectResult(boxes, meta)` |
| `seed_boxes(page_id)` | 上游已切好/校对好的定稿框（`(boxes, extra)`，没有则 `None`） |
| `seed_done(page_id)` | 上游是否把该页标记为已完成 |
| `order(boxes)` | 阅读顺序（中华字海按栏→行、汉字海按栏→y、康熙右起） |
| `export_image / export_images` | 按框裁图 |
| `refine_boxes / review_flags` | 墨迹贴合与「待查」标记（默认实现，书源可覆写） |

新增书源：写一个类实现以上方法，在 `pipelines/registry.py` 注册即可，界面自动出现。

### 取图与缓存

`core/imaging.py`：

* `embedded_ink(pdf, pno)`：取页内嵌原始 1bit 图（不重采样），按线程各持一份 PyMuPDF 文档，
  墨迹数组做 LRU（默认 4 页）。
* `render_gray(pdf, pno, zoom)`：彩色扫描件（康熙、汉语大字典）按 zoom 渲染灰度。
* 预览走 `downscale + JPEG`，裁字形走 `crop + PNG`。

### 字框存储

`backend/var/boxes/<book>/<page>.ndjson`（页号里的 `:` 等 Windows 非法字符会换成 `_`，
所以中华字海/康熙的 `1:84`、`07:100` 落成 `1_84.ndjson`、`07_100.ndjson`）：

```json
{"book":"hzh","page":"452","width":4172,"height":6148,
 "boxes":[{"x":163,"y":912,"w":123,"h":123,"source":"auto","seq":1,"col":1}],
 "extra":{"pitch":91.1,"review":[3,7]},
 "state":{"done":true,"suspect":false}}
```

`state.done` / `state.suspect` 驱动左栏状态点、`下一未完成` / `下一待查`。
每次保存追加一行到 `backend/var/edits.ndjson`（编辑流水，便于回溯）。
改动过才在 `backend/var/history/<book>/<page>/` 留快照（保留 200 份），供「编辑历史」恢复。

### 定稿数据（seed）

`yedict-data` 里各书源都有成稿：中华字海 `_zihai/editor/edits{,_v2}.json`、
汉字海 `_zihai/hh/ZI.ndjson`+`ZJ.ndjson`、康熙 `sources/字头清单.csv`。
`GET /pages/{p}/boxes` 的顺序是 **本地存盘 → 上游定稿 → 空**（前端拿到空才去检测），
返回值里的 `origin`（`local` / `seed` / `none`）与 `meta.seeded` 用于界面提示。
设置项 `use_seed` 可关闭。索引在进程内按文件路径缓存，首次打开某书约 0.1–0.8 s。

### 任务

`jobs.py` 用 2 线程执行批量检测/导出，任务状态在内存（进程重启即丢）。
单页操作是同步的（检测 0.3–4 s/页，看分辨率）。

## 前端

版式照搬 `_zihai/editor/index.html`：顶栏 + 画布 + 右侧固定面板，浅色为默认，黑夜模式整体反色。

* `App.svelte`：流程与状态（页切换、自动检测、存盘、完成、待查、导入导出、快捷键）。
* `Stage.svelte`：`<img>` + `<canvas>` 叠加层。坐标以**页像素**为准，绘制时乘 `画布宽 / 页宽`；
  缩放/平移在容器上做，重排时保持视窗中心不漂移（与校对台 `layout()` 同算法）。
  吸附用的墨迹掩码由当前显示的页图 `getImageData` 现场生成（阈值 160），
  `snapEdges` / `fitToInk` 逐行移植自校对台，所以「每条边只停在墨迹上」的行为一致。
* `PreviewPane.svelte`：300×300 的「当前框切出来的样子」，框外涂白 + 绿框，与校对台 `preview()` 一致。

坐标空间约定：前端拿到的是**整页灰度图**的像素坐标，`meta.width/height` 是它的尺寸；
预览图只是它的等比缩小版（默认宽 2200），框坐标不随预览尺寸变化。

## 与原桌面端的关系

原 `src/`（PySide6）已删除。保留下来的行为：

* 连通域 + 宽高区间的通用检测 → `core/detector.py`（汉语大字典在用）
* 自动纠斜 → `core/deskew.py`
* 阅读顺序（分栏 → 行 → x）→ `core/export.py`
* 覆盖度去重 → `core/box.py`

新增的行为（来自 `_zihai/editor` 校对台）：墨迹贴合、待查标记、完成状态、编辑流水、多卷。

## 配置来源

本地盘路径（数据仓库 `yedict-data`、各字典扫描 PDF）不写进代码也不提交，
统一放在仓库根的 `config.local.yaml`（`.gitignore` 已排除），模板是 `config.example.yaml`。
`backend/app/config.py` 的合并顺序：
`settings.json`（界面改的） → 环境变量 → `config.local.yaml` → 内置默认（只有页码，路径全空）。
