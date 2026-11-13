# Cutter · 字典切图工作台

把《中华字海》《汉字海》《康熙字典（内府本）》《汉语大字典》的扫描件切成字头图，
统一到一个 **FastAPI 后端 + Svelte 5 前端** 的本地 Web 应用里。

原来的 PySide6 桌面端（通用连通域检测 + 手动画框）已按其算法移植进后端并删除；
切图算法直接复用 `yedict-data/tools/` 里已经跑出结果的实现，保证与既有产物一致。

```
backend/                FastAPI 应用
  app/
    config.py           数据目录 / 书源 / 运行时目录
    models.py           API 请求响应模型
    store.py            字框 sidecar（每页一个 ndjson）+ 编辑流水
    jobs.py             批量检测 / 批量导出（线程池任务）
    core/               box 几何、去重、纠斜、连通域检测、导出排序、墨迹贴合、PDF 取图
    pipelines/          四个书源（zihai / hzh / kx / hd）
      upstream/         从 yedict-data/tools 逐字复制的算法实现
    api/                books / export / jobs / settings 路由
  tests/                pytest（合成 PDF，不依赖外部数据）
frontend/               Vite + Svelte 5 单页应用
  src/components/       CanvasEditor（画布画框）、PageList、Inspector、SettingsDialog
docs/                   架构与管线说明
scripts/                启动脚本
```

## 快速开始

```powershell
# 1) 后端依赖（uv 管理，生成 .venv 与 uv.lock）
uv sync

# 2) 前端依赖
cd frontend; npm install; cd ..

# 3) 开发：后端 8010 + 前端 5173（/api 已代理）
uv run uvicorn app.main:app --app-dir backend --reload --port 8010
cd frontend; npm run dev

# 4) 或者：构建前端后由后端一起托管
cd frontend; npm run build; cd ..
uv run uvicorn app.main:app --app-dir backend --port 8010
# 打开 http://127.0.0.1:8010/
```

也提供了脚本：

```powershell
.\scripts\dev.ps1      # 两个进程一起起（开发）
.\scripts\start.ps1    # 构建前端并单端口运行
```

## 数据在哪里

数据仓库（`yedict-data`，约 28.5 GB）和各字典的扫描 PDF **都不进本仓库**，
本地盘路径全部放在 `config.local.yaml`——这个文件**不提交**（见 `.gitignore`）。

```powershell
Copy-Item config.example.yaml config.local.yaml   # 然后填你的路径
```

```yaml
data_dir: ../yedict-data            # 或写绝对路径
books:
  zihai:
    volumes:
      "1": { pdf: "" }              # 中华字海1a.pdf
      "2": { pdf: "" }              # 中华字海2.pdf
  hzh:  { pdf: "" }
  kx:   { book_dir: "" }            # 13 册康熙扫描 PDF 所在目录
  hd:
    volumes:
      "1": { pdf: "" }              # 汉语大字典第一版
      "2": { pdf: "" }              # 汉语大字典第二版
```

优先级（从高到低）：界面「设置」存下的 `backend/var/settings.json` →
环境变量（`CUTTER_DATA_DIR` / `CUTTER_ZIHAI_PDF` / …）→ `config.local.yaml` →
代码内置默认（只含页码结构，路径全空）。

路径可以写绝对路径，也可以写相对仓库根。运行时产物（字框、编辑历史、导出、
对齐表）都在 `backend/var/`，同样已被 git 忽略。

## 操作方式（照搬《中华字海》字头框手动校对台）

界面与 `yedict-data/_zihai/editor/index.html` 同一套版式：左侧竖排工具条 + 大白页画布 + 右侧固定面板。

| 区域 | 作用 |
| --- | --- |
| 顶栏 | 书源切换、分卷、上/下一页、跳转、下一未完成、下一可疑页、下一待查框、本页完成、缩放、适合窗口、吸附墨迹、黑夜模式、标红待查、未保存提示、进度、导出/导入 JSON、本页还原 |
| 画布 | 空白处拖动＝拉新框；拖框体＝移动；**8 个手柄**（四角 + 四边中点）；拖动时按**连通块**吸附，`Alt` 临时关掉 |
| 右侧上 | 当前框「切出来的样子」小预览（≤152px 见方，跟着栏宽缩）、框坐标、贴合墨迹 / 连通吸附 / 删除框 / 新建框、连通间隙 |
| 右侧中 | **本页全部字**：本页每个字的切图网格（常驻，不用切标签），点一格跳回画布并选中该框 |
| 右侧下 | **参数 / 工具 / 说明**（默认折叠，`F2` 展开）：检测参数、合法性检查、编辑历史、对齐表、手工对齐、操作说明 |

本页全部字与工具：

* **本页全部字**：按「栏 → y → x」排每个字的切图，图上画出真正的切框边线，看得出这一刀偏没偏；
  点任一格跳回画布并选中该框，双击直接定位过去。格子大小可拖滑杆（拖到最左＝按可用面积自动铺满）。
* **连通吸附**：框只贴到「框内连通块（同一字的笔画并集）」的外接框上，拖动时同样按这个吸附；
  按钮按本页全部框来一次（`A`）。
* **合法性检查**：出界、过小、空白框、重复、重叠、尺寸离群；《汉字海》另做**编号检查**——
  条目号缺失/重复/倒序/跳号（跳号＝疑似漏切）、部首、笔画、码位缺失或格式错、字段条数与框数不一致。
  点任一问题直接跳到对应框。
* **编辑历史**：每次存盘留一份快照（最多 200 份），可「恢复到这一版」（只改内存，回车才写盘）；
  《汉字海》还能切到「上游记录」，看 `_zihai/hh/edits.ndjson` 里当时的人工改动（只读）。

框的颜色与校对台一致：**绿＝自动检测，橙＝改过的，红＝机器猜的可疑框**（勾「标红待查」才显示）。

快捷键：

| 键 | 作用 |
| --- | --- |
| `Ctrl+Z` | 撤销 |
| `Ctrl+Shift+Z` / `Ctrl+Y` | 重做 |
| `Enter` | 存盘、标记本页完成、翻到下一未完成页 |
| `←` `→` `↑` `↓` | 微调选中框 1px（`Shift` 为 10px）；未选中时 `←`/`→` 翻页 |
| `Tab` | 下一个框 |
| `F` | 贴合选中框到墨迹 |
| `A` | 本页所有框按连通块吸附 |
| 单按 `Shift` | 本页全部重贴（按墨迹外接框，不按连通块） |
| `F2` | 展开 / 收起「参数 / 工具 / 说明」 |
| `Delete` | 删除选中框 |
| `0` / `1` | 适合窗口 / 100% |
| `D` | 黑夜模式 |
| 滚轮 | 上下滚动；`Alt+滚轮` 以光标为中心缩放；中键 / `Space` 拖动平移 |

「拖动只改内存，按回车才写盘」：未写盘的页会在右上角显示「● 未保存」，`Enter` 时整批写入。

**撤销 / 重做**：`Ctrl+Z` 撤销、`Ctrl+Shift+Z`（或 `Ctrl+Y`）重做，顶栏也有按钮。
一次拖动（按下→松开）算一步；删除/新建/贴合/全部重贴/连通吸附/微调/还原/导入/恢复历史各自算一步。
撤销只改内存，同样要 `Enter` 才落盘。

## 默认载入已有定稿数据

各字典在 `yedict-data` 里都已有切好并校对过的成果，**打开页面时直接用它们**，
不再当场重跑检测（顶栏会显示「定稿」徽标，进度也按上游完成状态算）：

| 书源 | 定稿数据 | 说明 |
| --- | --- | --- |
| 中华字海 | `_zihai/editor/edits.json`（卷 1）、`edits_v2.json`（卷 2）+ `progress*.json` | 人工校对后的框；完成状态来自 progress |
| 汉字海 | `_zihai/hh/ZI.ndjson` + `ZJ.ndjson` | 最终 Z 编号成果，框上带 `ZI…/ZJ…` 编号 |
| 康熙字典 | `_up/kangxi/kangxi-1716/sources/字头清单.csv` | 46,960 条「字头」页内矩形（300dpi），排除「部首标题」 |
| 汉语大字典 | `_up/handa/data/hd1-heads.ndjson` | 第一版 52,053 条定稿字头框（300dpi）；第二版无定稿，按版面法现算 |

优先级：**本地存盘 > 上游定稿 > 自动检测**。在「设置」里关掉「上游定稿」即可回到从零检测。

## 自动落盘

改完即存：停手约 1 秒写盘，切页 / 切后台 / 关窗口前也会先写（`keepalive`）。
同一页的写**按页加锁串行**，写入是「唯一临时文件 + rename」的原子操作，
每次带 `rev` 版本号；别处也改了同一页时返回 409，界面让你选「覆盖保存」或「重新载入」。
自动落盘的快照每页限流 30 秒一份，手动保存（回车 / 本页完成）永远留快照。

## 四个书源

| 书源 | 检测算法 | 页号 | 备注 |
| --- | --- | --- | --- |
| 中华字海 `zihai` | `zihai_grid.find_heads`：三栏 → 行网格 → 栏左缘字头 | `卷:PDF页` | 卷 1/卷 2 多卷；卷 2 可配 `box_margin` 自动按墨迹收紧。**与上游 `_zihai/editor/cache` 的自动框逐页一致**（第 1 页 42 框、side 247/120 全对） |
| 汉字海 `hzh` | `hzh_crop.page_boxes`：栏左缘大字 + 右侧序号 | `PDF页` | 编号检查读 `_zihai/hh/fields*.ndjson`；历史读 `_zihai/hh/edits.ndjson` |
| 康熙字典 `kx` | `kx_locate.big_glyphs`：粗笔画连通块 | `分册:页` | 按位置号出 `K…` 隐编号 |
| 对齐表 | `align.py`：字海1.0 / 卷1 / 卷2 三方逐行核对（移植自 `_zihai/table`） | `页` | 只读核对，切图跳回编辑器 |
| 汉语大字典 `hd` | `hd_heads` 大字块 + 双栏 + 每栏最左一列（第二版可叠文字层） | `册:PDF页` | 两版版式相同、算法同一套；正文高度自适应，参数可调 |

细节见 [docs/PIPELINES.md](docs/PIPELINES.md)，架构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 检查

一条命令跑全部检查（ruff → pyright → pytest → svelte-check → 构建）：

```powershell
.\scripts\check.ps1        # 全跑
.\scripts\check.ps1 -Fast  # 只跑 lint + 类型
```

单独跑：

```powershell
uv run ruff check backend scripts
uv run ruff format --check backend scripts
uv run pyright
uv run pytest
cd frontend; npm run check
```

`backend/app/pipelines/upstream/` 是从 yedict-data 逐字复制的算法，**不参与** ruff / pyright
（避免为了风格去改上游代码）；其余代码按 ruff（E/F/W/I/UP/B/C4/SIM/RUF）与 pyright basic 检查。

## 测试

```powershell
uv run pytest
cd frontend; npm run build
```

后端测试用合成 PDF，覆盖：核心几何/纠斜/连通域、整页检测 → 保存 → 导出、任务、参数校验、
合法性检查（含汉字海编号）、编辑历史快照与恢复。
