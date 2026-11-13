# 待办 / 路线

## 已完成

**架构**
- [x] 四个书源（中华字海 / 汉字海 / 康熙字典 / 汉语大字典）统一到 FastAPI + Svelte 5
- [x] 切图算法复用上游（`backend/app/pipelines/upstream/`，含汉大 `hd_heads` / `hd_v2`）
- [x] 删除 PySide6 桌面端（算法移植到 `backend/app/core/`）
- [x] 检查器 `scripts/check.ps1`（ruff + format + pyright + pytest + svelte-check + build）

**界面**
- [x] 前端照搬 `_zihai/editor`：8 手柄、强吸附（`snapEdges`/`fitToInk`）、绿/橙/红框、黑夜模式
- [x] 顶栏搬进左栏（竖长页面纵向吃紧，横排顶栏白占 130px；画布高度 708 → 838）
- [x] 撤销 / 重做（Ctrl+Z、Ctrl+Shift+Z、Ctrl+Y）
- [x] 分卷下拉（中华字海 / 康熙 / 汉语大字典）
- [x] 本页工具：本页全部字、合法性检查（含汉字海编号）、编辑历史（快照 + 上游改动）
- [x] 本页工具：对齐表（见下）、手工对齐（见下）

**数据与落盘**
- [x] 默认载入上游定稿：中华字海 `edits.json` / 汉字海 `ZI·ZJ` / 康熙 `字头清单.csv` / 汉大 `hd1-heads.ndjson`
- [x] 汉语大字典两册（第一版 297–5106 offset 296、第二版 1–5127），两版同一套版面法
- [x] 汉大正文高度自适应（上游写死 40..100 只适合第一版，第二版会一字不出）+ 页脚忽略线
- [x] **自动落盘**：防抖 900ms、同页串行、`rev` 乐观并发、原子写（唯一临时文件 + rename）、
      卸载 keepalive、切页/切后台先写、快照限流 30s（手动保存永远留快照）

**对齐 / 核对**
- [x] 对齐表（对应 `_zihai/table`）：同端口按页现算，不生成 36MB HTML；框数本地存盘优先
- [x] 对齐表：字海1.0 雪碧图（Z/P 系没有码位字，第一列只能靠书影）
- [x] 手工对齐（对应旧 `ui_table/table_view.py`）：图来自当前页或本地文件夹 + 粘贴文本或 **TSV**，
      `Ctrl+I` 合并 / `Ctrl+U` 拆分 / `Shift+↑↓` 换行 / `↑图↓图` 只调图 / 导出 **TSV**

## 下一步

- [ ] 康熙位置号（`K…`）与 `build_kx_sprites.py` 的雪碧图生产对齐（当前按页内序号重排）
- [ ] 汉字海：`suspects`（疑似漏切的属性行）在画布上标出来
- [ ] 雪碧图检查台（对应 `_zihai/sprite_review`）
- [ ] 导出直接写入 `sprites_z` / `sprites_kx`（走 `add_sprites.py` 的占位逻辑）
- [ ] 汉字海 seed 带上 entry/部首/笔画/码位，供画布内直接显示与编辑
- [ ] 手工对齐：本地文件夹用系统文件选择框（现在是手输路径）
- [ ] 汉大第二版：文字层只覆盖少量字头（PP-OCRv3 对孤立大字约 17%），需要更强的定位
