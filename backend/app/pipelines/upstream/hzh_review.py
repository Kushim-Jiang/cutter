#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《汉字海》字头框复核前端（只认坐标，切图按需从 PDF 现渲染，不落盘）。

    python tools/hzh_review.py                 # 默认 8765 端口，自动开浏览器
    python tools/hzh_review.py --port 9000 --no-open

左边是整页书影（绿框 = 已定位的字头，橙虚线 = 疑似漏切），右边是各框从 PDF 现切的小图。
点框或点小图打「有问题」标记，标记存 <out>/flags.json（`{页: {序号: 1}}`）。

快捷键：← → 翻页　F 标记/取消标记最后一个看过的小图　Esc 清除焦点
"""
import argparse
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PDF = ""  # 路径由 config.local.yaml / 调用方给出
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "_zihai", "hh")
PAGE_DPI = 110          # 整页预览的渲染分辨率
BOX_DPI = 300           # 单框小图的渲染分辨率
PAD = 0.28              # 单框小图外扩（比例，按行距 pitch）
_lock = threading.Lock()
_doc = None
_page_jpg = {}


# ---------------------------------------------------------------- 数据

def load_index():
    path = os.path.join(DATA, "index.ndjson")
    if not os.path.exists(path):
        raise SystemExit(f"缺少 {path}，先跑 tools/hzh_crop.py")
    pages = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            pages.setdefault(r["page"], []).append(r)
    for v in pages.values():
        v.sort(key=lambda r: r["seq"])
    return pages


def load_json_ndjson(name, key):
    path = os.path.join(DATA, name)
    out = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                out.setdefault(r[key], []).append(r)
    return out


def load_flags():
    path = os.path.join(DATA, "flags.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_flags(flags):
    path = os.path.join(DATA, "flags.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(flags, f, ensure_ascii=False, indent=0, sort_keys=True)
    os.replace(tmp, path)


def page_png(pno):
    """整页预览（灰度 JPEG），首次访问时渲染并缓存。"""
    with _lock:
        body = _page_jpg.get(pno)
        if body is not None:
            return body
    import fitz
    page = _doc[pno - 1]
    zoom = PAGE_DPI / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY)
    body = pix.tobytes("jpeg")
    with _lock:
        if len(_page_jpg) > 24:
            _page_jpg.pop(next(iter(_page_jpg)))
        _page_jpg[pno] = body
    return body


def box_png(pno, seq):
    """单框小图：直接从 PDF 现切（正方形留白），不落盘。"""
    import fitz
    b = None
    for r in index.get(pno, []):
        if r["seq"] == seq:
            b = r
            break
    if b is None:
        for s in suspects.get(pno, []):
            if s.get("seq") == seq:
                b = s
                break
    if b is None:
        return None
    page = _doc[pno - 1]
    img = page.get_images(full=True)[0]
    iw, ih = img[2], img[3]
    pitch = b.get("pitch") or int(1.4 * max(b["w"], b["h"]))
    pad = PAD * pitch
    side = max(b["w"], b["h"]) + 2 * pad
    cx, cy = b["x"] + b["w"] / 2.0, b["y"] + b["h"] / 2.0
    x0, y0 = max(0, cx - side / 2.0), max(0, cy - side / 2.0)
    x1, y1 = min(iw, cx + side / 2.0), min(ih, cy + side / 2.0)
    sx, sy = page.rect.width / iw, page.rect.height / ih
    clip = fitz.Rect(x0 * sx, y0 * sy, x1 * sx, y1 * sy)
    zoom = BOX_DPI / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, colorspace=fitz.csGRAY)
    return pix.tobytes("png")


HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>汉字海 字头框复核</title>
<style>
:root { --line:#d8d8d8; }
* { box-sizing:border-box; }
body { margin:0; font:14px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; color:#222; background:#fafafa; }
header { position:sticky; top:0; z-index:9; display:flex; gap:8px; align-items:center; flex-wrap:wrap;
         padding:8px 14px; background:#fff; border-bottom:1px solid var(--line); }
header b { font-size:16px; margin-right:6px; }
button, input { font:inherit; }
button { padding:4px 10px; border:1px solid #bbb; background:#fff; border-radius:5px; cursor:pointer; }
button:hover { background:#f0f0f0; }
input[type=number] { width:84px; padding:3px 6px; border:1px solid #bbb; border-radius:5px; }
.legend { color:#666; font-size:12px; }
.sp { margin-left:auto; color:#555; }
main { display:flex; gap:12px; padding:12px; align-items:flex-start; }
#stage { position:relative; flex:0 0 auto; }
#stage img { display:block; border:1px solid var(--line); }
.box { position:absolute; border:2px solid rgba(20,150,60,.9); background:rgba(20,150,60,.05); cursor:pointer; }
.box.sus { border-color:rgba(230,120,0,.95); border-style:dashed; background:rgba(230,120,0,.07); }
.box.flag { border-color:#e00; background:rgba(230,0,0,.14); }
.box span { position:absolute; left:-2px; top:-17px; font-size:11px; line-height:15px; color:#fff;
            background:rgba(20,150,60,.9); padding:0 3px; border-radius:3px; white-space:nowrap; }
.box.sus span { background:rgba(230,120,0,.95); }
.box.flag span { background:#e00; }
#crops { flex:1 1 auto; display:grid; grid-template-columns:repeat(auto-fill,minmax(104px,1fr));
         gap:6px; align-content:start; }
.cell { position:relative; border:2px solid #ccc; background:#fff; border-radius:4px; cursor:pointer;
        aspect-ratio:1/1; display:flex; align-items:center; justify-content:center; overflow:hidden; }
.cell img { max-width:100%; max-height:100%; display:block; }
.cell.flag { border-color:#e00; }
.cell em { position:absolute; left:3px; top:1px; font-size:11px; font-style:normal; color:#999; }
.cell.flag em { color:#e00; font-weight:700; }
#toast { position:fixed; right:16px; bottom:16px; background:#333; color:#fff; padding:6px 12px;
         border-radius:6px; opacity:0; transition:opacity .2s; pointer-events:none; }
</style></head><body>
<header>
  <b>汉字海 字头框复核</b>
  <button id="prev">← 上一页</button>
  <button id="next">下一页 →</button>
  <input type="number" id="gopdf" placeholder="PDF 页"><button id="goPdf">跳转</button>
  <input type="number" id="goprint" placeholder="印刷页"><button id="goPrint">跳转</button>
  <span class="legend">绿框=字头　橙虚线=疑似漏切　红=已标记</span>
  <span class="sp" id="stat"></span>
</header>
<main>
  <div id="stage"></div>
  <div id="crops"></div>
</main>
<div id="toast"></div>
<script>
const OFFSET = 451;
const state = { pages: [], idx: 0, data: null };
const $ = (s) => document.querySelector(s);

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.style.opacity = 1;
  clearTimeout(t._h); t._h = setTimeout(() => { t.style.opacity = 0; }, 1100);
}

async function boot() {
  state.pages = await (await fetch("/api/pages")).json();
  const want = parseInt(new URLSearchParams(location.search).get("page") || 0);
  const i = state.pages.indexOf(want);
  state.idx = i >= 0 ? i : 0;
  load();
}

async function load() {
  const pno = state.pages[state.idx];
  const d = await (await fetch("/api/page?page=" + pno)).json();
  state.data = d;
  history.replaceState(null, "", "?page=" + pno);
  const nf = d.boxes.filter(b => b.flag).length + d.suspects.filter(b => b.flag).length;
  $("#stat").textContent = `PDF ${pno}　印刷 ${pno - OFFSET}　第 ${state.idx + 1}/${state.pages.length} 页　`
    + `框 ${d.boxes.length}　待查 ${d.suspects.length}　已标 ${nf}`;

  const stage = $("#stage");
  stage.innerHTML = "";
  const img = new Image();
  img.src = "/page/" + pno + ".jpg";
  img.onload = () => {
    const sx = img.clientWidth / d.w, sy = img.clientHeight / d.h;
    const add = (b, cls) => {
      const el = document.createElement("div");
      el.className = "box " + cls + (b.flag ? " flag" : "");
      el.style.left = (b.x * sx) + "px";
      el.style.top = (b.y * sy) + "px";
      el.style.width = Math.max(8, b.w * sx) + "px";
      el.style.height = Math.max(8, b.h * sy) + "px";
      el.innerHTML = "<span>" + (b.seq ?? "?") + "</span>";
      el.title = (cls ? "疑似漏切" : "字头") + " " + (b.seq ?? "");
      el.onclick = () => toggle(b);
      stage.appendChild(el);
    };
    d.boxes.forEach(b => add(b, ""));
    d.suspects.forEach(b => add(b, "sus"));
  };
  stage.appendChild(img);

  const crops = $("#crops");
  crops.innerHTML = "";
  const cells = d.boxes.concat(d.suspects);
  cells.forEach(b => {
    const el = document.createElement("div");
    el.className = "cell" + (b.flag ? " flag" : "");
    el.innerHTML = `<em>${b.seq ?? "?"}</em>`
      + `<img loading="lazy" src="/box/${pno}/${b.seq}?w=${b.w}&h=${b.h}">`;
    el.onclick = () => toggle(b);
    crops.appendChild(el);
  });
}

async function toggle(b) {
  if (b.seq == null) { toast("这个位置没有序号，改坐标后重跑脚本"); return; }
  const pno = state.pages[state.idx];
  const r = await fetch("/api/flag", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ page: pno, seq: b.seq, flag: !b.flag }),
  });
  const j = await r.json();
  b.flag = j.flag;
  toast(`${pno} #${b.seq} ${j.flag ? "已标记" : "取消标记"}`);
  load();
}

function jump(pno) {
  const i = state.pages.indexOf(pno);
  if (i >= 0) { state.idx = i; load(); } else { toast("页码范围 452–2648"); }
}

$("#prev").onclick = () => { if (state.idx > 0) { state.idx--; load(); } };
$("#next").onclick = () => { if (state.idx < state.pages.length - 1) { state.idx++; load(); } };
$("#goPdf").onclick = () => jump(parseInt($("#gopdf").value));
$("#goPrint").onclick = () => jump(parseInt($("#goprint").value) + OFFSET);
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return;
  if (e.key === "ArrowLeft") $("#prev").click();
  if (e.key === "ArrowRight") $("#next").click();
});
boot();
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", cache="no-store"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", cache)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            return self._send(200, HTML, "text/html; charset=utf-8")
        if u.path == "/api/pages":
            return self._send(200, pages_sorted)
        if u.path == "/api/page":
            pno = int(q["page"][0])
            boxes = [dict(b, flag=bool(flags.get(str(pno), {}).get(str(b["seq"])))) for b in index.get(pno, [])]
            sus = [dict(s, flag=bool(flags.get(str(pno), {}).get(s.get("seq")))) for s in suspects.get(pno, [])]
            img = _doc[pno - 1].get_images(full=True)[0]
            return self._send(200, dict(page=pno, boxes=boxes, suspects=sus, w=img[2], h=img[3]))
        if u.path.startswith("/page/"):
            pno = int(u.path[6:].split(".")[0])
            return self._send(200, page_png(pno), "image/jpeg", "max-age=600")
        if u.path.startswith("/box/"):
            parts = u.path[5:].split("/")
            pno, seq = int(parts[0]), int(parts[1])
            body = box_png(pno, seq)
            if body is None:
                return self._send(404, b"", "text/plain")
            return self._send(200, body, "image/png", "max-age=600")
        return self._send(404, b"", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path != "/api/flag":
            return self._send(404, b"", "text/plain")
        n = int(self.headers.get("content-length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        pno, seq = int(req["page"]), int(req["seq"])
        with _lock:
            per = flags.setdefault(str(pno), {})
            if req.get("flag"):
                per[str(seq)] = 1
            else:
                per.pop(str(seq), None)
            if not per:
                flags.pop(str(pno), None)
            save_flags(flags)
        return self._send(200, {"flag": bool(req.get("flag"))})


index = None
pages_sorted = []
suspects = {}
flags = {}


def load_all():
    global index, pages_sorted, suspects, flags
    index = load_index()
    pages_sorted = sorted(index)
    suspects = load_json_ndjson("suspects.ndjson", "page")
    flags = load_flags()


def main():
    global _doc, DATA
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data", default=DATA, help="坐标目录（默认 _zihai/hh）")
    ap.add_argument("--pdf", default=PDF)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    import fitz
    DATA = args.data
    _doc = fitz.open(args.pdf)
    load_all()
    url = f"http://127.0.0.1:{args.port}/"
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"{len(pages_sorted)} 页 / {sum(len(v) for v in index.values())} 个框；"
          f"待查 {sum(len(v) for v in suspects.values())}；已标记 {sum(len(v) for v in flags.values())}")
    print(f"打开 {url}")
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
