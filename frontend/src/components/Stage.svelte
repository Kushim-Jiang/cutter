<script>
  import { untrack } from 'svelte'
  import { autoRetry } from '../lib/retry.js'

  let {
    imageUrl = '',
    pageWidth = 0,
    pageHeight = 0,
    boxes = $bindable([]),
    sel = $bindable(-1),
    snapOn = true,
    dark = false,
    showRev = true,
    review = [],
    gap = 2,
    onchange = () => {},
    onbeforechange = () => {},
  } = $props()

  let stageEl = $state(null)
  let wrapEl = $state(null)
  let imgEl = $state(null)
  let ovEl = $state(null)

  let sw = $state(0)
  let sh = $state(0)
  let zoomVal = $state(1)
  let panX = $state(0)
  let panY = $state(0)
  let spaceDown = false
  let panning = null
  let drag = null
  let layoutAnchor = null

  // 墨迹掩码（吸附用）
  let inkMask = null
  let maskW = 0
  let maskH = 0
  let maskScale = 1

  // 连通块（8 连通）：吸附时框只贴到「同一个字的笔画并集」的外接框上，
  // 不会停在一根悬空的细线或一点纸纹上。
  let labels = null
  let comps = null
  let compsN = 0

  const GROW_SLACK = 0.15
  const SNAP_PAD = 2
  const OWN_FIT = 0.5      // 连通块外接框有一半以上在框内 → 归这个框
  const OWN_MIN = 0.15     // 一个都不满足时，退而取重叠最多的那块（至少 15%）
  const COMP_MIN_AREA = 6  // 比这还小的当纸纹
  const COMP_SPAN = 2.2    // 比框大这么多倍的（版框线、整行注文）不抢
  const CURSORS = {
    move: 'move',
    n: 'ns-resize', s: 'ns-resize', e: 'ew-resize', w: 'ew-resize',
    nw: 'nwse-resize', se: 'nwse-resize', ne: 'nesw-resize', sw: 'nesw-resize',
  }

  function ctx() {
    return ovEl ? ovEl.getContext('2d') : null
  }
  /** 显示像素 / 页像素 */
  function k() {
    return ovEl && pageWidth ? ovEl.width / pageWidth : 1
  }
  function baseScale() {
    if (!stageEl || !sw) return 1
    return Math.max(80, stageEl.clientWidth - 12) / sw
  }

  export function fit() {
    if (!stageEl || !sw) return
    const s = Math.min((stageEl.clientWidth - 12) / sw, (stageEl.clientHeight - 12) / sh)
    layoutAnchor = null
    zoomVal = Math.max(0.2, Math.min(8, s / baseScale()))
    panX = 0
    panY = 0
    layout()
  }

  export function setZoom(z, clientX = null, clientY = null) {
    if (!sw) return
    z = Math.max(0.2, Math.min(8, z))
    if (Math.abs(z - zoomVal) < 1e-6) return
    if (clientX != null) {
      const r = stageEl.getBoundingClientRect()
      layoutAnchor = { x: clientX - r.left, y: clientY - r.top }
    } else layoutAnchor = null
    zoomVal = z
    layout()
    layoutAnchor = null
  }

  export function getZoom() {
    return zoomVal
  }

  export function focusBox(index) {
    if (!boxes.length) return
    sel = Math.max(0, Math.min(boxes.length - 1, index))
    const b = boxes[sel]
    const kk = k()
    panX = stageEl.clientWidth / 2 - (b.x + b.w / 2) * kk
    panY = stageEl.clientHeight / 2 - (b.y + b.h / 2) * kk
    clampPan()
    applyTransform()
    draw()
  }

  function applyTransform() {
    if (wrapEl) wrapEl.style.transform = `translate(${Math.round(panX)}px,${Math.round(panY)}px)`
  }

  function clampPan() {
    if (!stageEl || !ovEl) return
    const vw = stageEl.clientWidth
    const vh = stageEl.clientHeight
    const dw = ovEl.width
    const dh = ovEl.height
    panX = dw <= vw ? Math.round((vw - dw) / 2) : Math.min(0, Math.max(vw - dw, panX))
    panY = dh <= vh ? Math.round((vh - dh) / 2) : Math.min(0, Math.max(vh - dh, panY))
  }

  function layout() {
    if (!stageEl || !imgEl || !sw) return
    const vw = Math.max(60, stageEl.clientWidth)
    const vh = Math.max(60, stageEl.clientHeight)
    const ax = layoutAnchor ? layoutAnchor.x : vw / 2
    const ay = layoutAnchor ? layoutAnchor.y : vh / 2
    let fx = 0
    let fy = 0
    if (ovEl && ovEl.width > 0 && ovEl.height > 0) {
      fx = (ax - panX) / ovEl.width
      fy = (ay - panY) / ovEl.height
    }
    const scale = baseScale() * zoomVal
    const dw = Math.max(1, Math.round(sw * scale))
    const dh = Math.max(1, Math.round(sh * scale))
    imgEl.style.width = `${dw}px`
    imgEl.style.height = `${dh}px`
    ovEl.width = dw
    ovEl.height = dh
    ovEl.style.width = `${dw}px`
    ovEl.style.height = `${dh}px`
    wrapEl.style.width = `${dw}px`
    wrapEl.style.height = `${dh}px`
    panX = ax - fx * dw
    panY = ay - fy * dh
    clampPan()
    applyTransform()
    draw()
  }

  function onImageLoad() {
    if (!imgEl) return
    sw = imgEl.naturalWidth || pageWidth
    sh = imgEl.naturalHeight || pageHeight
    buildMask()
    buildLabels()
    panX = 0
    panY = 0
    layout()
  }

  function buildMask() {
    if (!imgEl || !imgEl.naturalWidth) return
    const c = document.createElement('canvas')
    c.width = imgEl.naturalWidth
    c.height = imgEl.naturalHeight
    const x = c.getContext('2d', { willReadFrequently: true })
    x.drawImage(imgEl, 0, 0)
    let data
    try {
      data = x.getImageData(0, 0, c.width, c.height).data
    } catch {
      return
    }
    maskW = c.width
    maskH = c.height
    maskScale = pageWidth / maskW
    inkMask = new Uint8Array(maskW * maskH)
    for (let i = 0, j = 0; i < data.length; i += 4, j++) {
      inkMask[j] = data[i] < 160 ? 1 : 0
    }
  }

  /** 8 连通标记：一次扫一遍，之后吸附只看连通块的外接框。 */
  function buildLabels() {
    labels = null
    comps = null
    compsN = 0
    if (!inkMask || !maskW || !maskH) return
    const W = maskW
    const H = maskH
    const lab = new Int32Array(W * H).fill(-1)
    const cx0 = []
    const cy0 = []
    const cx1 = []
    const cy1 = []
    const area = []
    let stack = new Int32Array(1 << 13)
    let sp = 0
    const push = (v) => {
      if (sp === stack.length) {
        const grown = new Int32Array(stack.length * 2)
        grown.set(stack)
        stack = grown
      }
      stack[sp++] = v
    }
    const total = lab.length
    let n = 0
    for (let i = 0; i < total; i++) {
      if (!inkMask[i] || lab[i] >= 0) continue
      const id = n++
      cx0.push(W)
      cy0.push(H)
      cx1.push(-1)
      cy1.push(-1)
      area.push(0)
      sp = 0
      push(i)
      lab[i] = id
      while (sp) {
        const p = stack[--sp]
        const px = p % W
        const py = (p - px) / W
        if (px < cx0[id]) cx0[id] = px
        if (py < cy0[id]) cy0[id] = py
        if (px > cx1[id]) cx1[id] = px
        if (py > cy1[id]) cy1[id] = py
        area[id]++
        for (let dy = -1; dy <= 1; dy++) {
          const ny = py + dy
          if (ny < 0 || ny >= H) continue
          for (let dx = -1; dx <= 1; dx++) {
            const nx = px + dx
            if (nx < 0 || nx >= W || (dx === 0 && dy === 0)) continue
            const q = ny * W + nx
            if (inkMask[q] && lab[q] < 0) {
              lab[q] = id
              push(q)
            }
          }
        }
      }
    }
    labels = lab
    comps = { x0: cx0, y0: cy0, x1: cx1, y1: cy1, area }
    compsN = n
  }

  /** 归属于这个框的连通块：外接框一半以上落在框内（一个都没有时取重叠最多的那块）。 */
  function ownedComps(b) {
    const out = []
    if (!comps || !compsN) return out
    const L = Math.max(0, Math.round(b.x / maskScale))
    const T = Math.max(0, Math.round(b.y / maskScale))
    const R = Math.min(maskW - 1, Math.round((b.x + b.w) / maskScale))
    const B = Math.min(maskH - 1, Math.round((b.y + b.h) / maskScale))
    if (R <= L || B <= T) return out
    const bw = R - L + 1
    const bh = B - T + 1
    let best = -1
    let bestOv = 0
    let bestFit = 0
    for (let i = 0; i < compsN; i++) {
      const x0 = comps.x0[i]
      if (x0 > R || comps.x1[i] < L) continue
      const y0 = comps.y0[i]
      if (y0 > B || comps.y1[i] < T) continue
      if (comps.area[i] < COMP_MIN_AREA) continue
      const w = comps.x1[i] - x0 + 1
      const h = comps.y1[i] - y0 + 1
      // 版框线、整行注文这种「比框大得多」的块不参与，否则框会一下弹到整页
      if (w > Math.max(24, bw * COMP_SPAN) || h > Math.max(24, bh * COMP_SPAN)) continue
      const iw = Math.min(comps.x1[i], R) - Math.max(x0, L) + 1
      const ih = Math.min(comps.y1[i], B) - Math.max(y0, T) + 1
      const ov = iw * ih
      const fit = ov / (w * h)
      if (fit >= OWN_FIT) out.push(i)
      if (ov > bestOv) {
        bestOv = ov
        best = i
        bestFit = fit
      }
    }
    if (!out.length && best >= 0 && bestFit >= OWN_MIN) out.push(best)
    return out
  }

  /** 连通吸附：把被拖的那几条边贴到「框内连通块并集」的外接框上。 */
  function snapConnected(b, edges, ref) {
    if (!snapOn || !labels) return false
    const own = ownedComps(ref || b)
    if (!own.length) return false
    let x0 = Infinity
    let y0 = Infinity
    let x1 = -Infinity
    let y1 = -Infinity
    for (const i of own) {
      if (comps.x0[i] < x0) x0 = comps.x0[i]
      if (comps.y0[i] < y0) y0 = comps.y0[i]
      if (comps.x1[i] > x1) x1 = comps.x1[i]
      if (comps.y1[i] > y1) y1 = comps.y1[i]
    }
    const nx0 = x0 * maskScale
    const ny0 = y0 * maskScale
    const nx1 = (x1 + 1) * maskScale
    const ny1 = (y1 + 1) * maskScale
    const w = edges.includes('l') ? nx1 - nx0 : b.w
    const h = edges.includes('t') ? ny1 - ny0 : b.h
    if (w < 4 * maskScale || h < 4 * maskScale) return false
    if (edges.includes('l')) {
      b.w += b.x - nx0
      b.x = nx0
    }
    if (edges.includes('r')) b.w = nx1 - b.x
    if (edges.includes('t')) {
      b.h += b.y - ny0
      b.y = ny0
    }
    if (edges.includes('b')) b.h = ny1 - b.y
    if (b.w <= 0 || b.h <= 0) return false
    return true
  }

  function colCount(x, y0, y1) {
    if (x < 0 || x >= maskW) return 0
    y0 = Math.max(0, y0)
    y1 = Math.min(maskH - 1, y1)
    let n = 0
    for (let y = y0; y <= y1; y++) if (inkMask[y * maskW + x]) n++
    return n
  }

  function rowCount(y, x0, x1) {
    if (y < 0 || y >= maskH) return 0
    x0 = Math.max(0, x0)
    x1 = Math.min(maskW - 1, x1)
    const base = y * maskW
    let n = 0
    for (let x = x0; x <= x1; x++) if (inkMask[base + x]) n++
    return n
  }

  function fitToInk(b) {
    if (!inkMask) return
    const L = Math.max(0, Math.round(b.x / maskScale))
    const T = Math.max(0, Math.round(b.y / maskScale))
    const R = Math.min(maskW - 1, Math.round((b.x + b.w) / maskScale))
    const B = Math.min(maskH - 1, Math.round((b.y + b.h) / maskScale))
    const hTh = Math.max(2, Math.round(0.03 * (B - T)))
    const wTh = Math.max(2, Math.round(0.03 * (R - L)))
    let x0 = null
    let x1 = null
    let y0 = null
    let y1 = null
    for (let x = L; x <= R; x++) {
      if (colCount(x, T, B) >= hTh) {
        if (x0 === null) x0 = x
        x1 = x
      }
    }
    for (let y = T; y <= B; y++) {
      if (rowCount(y, L, R) >= wTh) {
        if (y0 === null) y0 = y
        y1 = y
      }
    }
    if (x0 === null || y0 === null) return
    b.x = x0 * maskScale
    b.y = y0 * maskScale
    b.w = (x1 - x0 + 1) * maskScale
    b.h = (y1 - y0 + 1) * maskScale
  }

  /** 强吸附：把被拖的边贴到光标那一侧最近的墨迹行列上 */
  function snapEdges(b, edges) {
    if (!snapOn || !inkMask) return
    const L0 = Math.max(0, Math.round(b.x / maskScale))
    const T0 = Math.max(0, Math.round(b.y / maskScale))
    const R0 = Math.min(maskW - 1, Math.round((b.x + b.w) / maskScale))
    const B0 = Math.min(maskH - 1, Math.round((b.y + b.h) / maskScale))
    if (R0 - L0 < 3 || B0 - T0 < 3) return
    const hTh = Math.max(2, Math.round(0.03 * (B0 - T0)))
    const wTh = Math.max(2, Math.round(0.03 * (R0 - L0)))
    let L = L0
    let T = T0
    let R = R0
    let B = B0
    if (edges.includes('l')) {
      for (let x = L0; x <= R0; x++) {
        if (colCount(x, T0, B0) >= hTh) {
          L = x
          break
        }
      }
    }
    if (edges.includes('r')) {
      for (let x = R0; x >= L0; x--) {
        if (colCount(x, T0, B0) >= hTh) {
          R = x + 1
          break
        }
      }
    }
    if (edges.includes('t')) {
      for (let y = T0; y <= B0; y++) {
        if (rowCount(y, L, R - 1) >= wTh) {
          T = y
          break
        }
      }
    }
    if (edges.includes('b')) {
      for (let y = B0; y >= T0; y--) {
        if (rowCount(y, L, R - 1) >= wTh) {
          B = y + 1
          break
        }
      }
    }
    if (R - L < 4 || B - T < 4) return
    if (edges.includes('l')) {
      const nx = L * maskScale
      b.w += b.x - nx
      b.x = nx
    }
    if (edges.includes('r')) b.w = R * maskScale - b.x
    if (edges.includes('t')) {
      const ny = T * maskScale
      b.h += b.y - ny
      b.y = ny
    }
    if (edges.includes('b')) b.h = B * maskScale - b.y
  }

  /** 吸附墨迹时框外留的白边：人工确认过的框实测就是「墨迹外接框 + 每边 2px」 */
  export function refit(b) {
    if (!inkMask) return
    fitToInk(b)
    padBox(b, (snapOn ? SNAP_PAD : 0) - (gap || 0))
  }

  /** 连通吸附一个框：只留框内那几个连通块（一个字的笔画并集），返回是否改动。 */
  export function refitConnected(b) {
    if (!labels) return false
    const own = ownedComps(b)
    if (!own.length) return false
    let x0 = Infinity
    let y0 = Infinity
    let x1 = -Infinity
    let y1 = -Infinity
    for (const i of own) {
      if (comps.x0[i] < x0) x0 = comps.x0[i]
      if (comps.y0[i] < y0) y0 = comps.y0[i]
      if (comps.x1[i] > x1) x1 = comps.x1[i]
      if (comps.y1[i] > y1) y1 = comps.y1[i]
    }
    b.x = x0 * maskScale
    b.y = y0 * maskScale
    b.w = (x1 - x0 + 1) * maskScale
    b.h = (y1 - y0 + 1) * maskScale
    padBox(b, (snapOn ? SNAP_PAD : 0) - (gap || 0))
    return true
  }

  function padBox(b, n) {
    b.x -= n
    b.y -= n
    b.w += 2 * n
    b.h += 2 * n
  }

  function handles(x, y, w, h) {
    return [
      [x, y], [x + w / 2, y], [x + w, y],
      [x + w, y + h / 2], [x + w, y + h],
      [x + w / 2, y + h], [x, y + h], [x, y + h / 2],
    ]
  }

  function draw() {
    const c = ctx()
    if (!c || !pageWidth || !ovEl.width) return
    c.clearRect(0, 0, ovEl.width, ovEl.height)
    const kk = k()
    const isDark = dark
    boxes.forEach((b, i) => {
      const x = b.x * kk
      const y = b.y * kk
      const w = b.w * kk
      const h = b.h * kk
      const isSel = i === sel
      const isRev = showRev && review.includes(i)
      c.lineWidth = isSel ? 2.5 : isRev ? 2 : 1.4
      c.strokeStyle = isSel ? '#ff6b00' : isRev ? '#e03131' : b.edited ? '#e8710a' : isDark ? '#3ddc84' : '#12a150'
      c.strokeRect(x + 0.5, y + 0.5, w, h)
      if (isRev && !isSel) {
        c.fillStyle = 'rgba(224,49,49,.10)'
        c.fillRect(x, y, w, h)
      }
      if (isSel) {
        c.fillStyle = 'rgba(255,107,0,.12)'
        c.fillRect(x, y, w, h)
        handles(x, y, w, h).forEach(([hx, hy]) => {
          c.fillStyle = '#fff'
          c.strokeStyle = '#ff6b00'
          c.lineWidth = 1.5
          c.beginPath()
          c.rect(hx - 4.5, hy - 4.5, 9, 9)
          c.fill()
          c.stroke()
        })
      }
    })
    if (drag && drag.mode === 'new') {
      const r = normRect(drag)
      c.strokeStyle = '#0b6bcb'
      c.lineWidth = 2
      c.setLineDash([6, 4])
      c.strokeRect(r.x * kk, r.y * kk, r.w * kk, r.h * kk)
      c.setLineDash([])
    }
  }

  function normRect(d) {
    return {
      x: Math.min(d.x0, d.x1), y: Math.min(d.y0, d.y1),
      w: Math.abs(d.x1 - d.x0), h: Math.abs(d.y1 - d.y0),
    }
  }

  function toPage(ev) {
    const r = ovEl.getBoundingClientRect()
    const s = pageWidth / r.width
    return { x: (ev.clientX - r.left) * s, y: (ev.clientY - r.top) * s }
  }

  function hit(p, b) {
    const kk = k()
    const X = b.x * kk
    const Y = b.y * kk
    const W2 = b.w * kk
    const H2 = b.h * kk
    const mx = p.x * kk
    const my = p.y * kk
    const t = 9
    const nL = Math.abs(mx - X) < t
    const nR = Math.abs(mx - (X + W2)) < t
    const nT = Math.abs(my - Y) < t
    const nB = Math.abs(my - (Y + H2)) < t
    if (mx < X - t || mx > X + W2 + t || my < Y - t || my > Y + H2 + t) return null
    if (nL && nT) return 'nw'
    if (nR && nT) return 'ne'
    if (nL && nB) return 'sw'
    if (nR && nB) return 'se'
    if (nT) return 'n'
    if (nB) return 's'
    if (nL) return 'w'
    if (nR) return 'e'
    if (mx > X && mx < X + W2 && my > Y && my < Y + H2) return 'move'
    return null
  }

  function markEdited(i) {
    if (i >= 0 && boxes[i]) boxes[i].edited = true
    boxes = boxes
  }

  function mousedown(ev) {
    if (!ovEl || !sw) return
    if (ev.button === 1 || (ev.button === 0 && spaceDown)) {
      panning = { x: ev.clientX, y: ev.clientY, px: panX, py: panY }
      return
    }
    if (ev.button !== 0) return
    const p = toPage(ev)
    let best = -1
    let mode = null
    for (let i = boxes.length - 1; i >= 0; i--) {
      const m = hit(p, boxes[i])
      if (m) {
        best = i
        mode = m
        break
      }
    }
    if (best >= 0) {
      sel = best
      onbeforechange()
      drag = { mode, i: best, x0: p.x, y0: p.y, orig: { ...boxes[best] }, moved: false }
    } else {
      sel = -1
      onbeforechange()
      drag = { mode: 'new', x0: p.x, y0: p.y, x1: p.x, y1: p.y, moved: false }
    }
    draw()
  }

  function mousemove(ev) {
    if (!ovEl || !sw) return
    if (panning) {
      panX = panning.px + (ev.clientX - panning.x)
      panY = panning.py + (ev.clientY - panning.y)
      clampPan()
      applyTransform()
      return
    }
    if (!drag) {
      if (spaceDown) {
        stageEl.style.cursor = 'grab'
        return
      }
      const p = toPage(ev)
      let cur = 'crosshair'
      for (let i = boxes.length - 1; i >= 0; i--) {
        const m = hit(p, boxes[i])
        if (m) {
          cur = CURSORS[m] || 'default'
          break
        }
      }
      stageEl.style.cursor = cur
      return
    }
    const p = toPage(ev)
    if (!drag.moved) {
      const d = Math.hypot(p.x - drag.x0, p.y - drag.y0) * k()
      if (d < 3) return
      drag.moved = true
    }
    const noSnap = ev.altKey
    if (drag.mode === 'new') {
      drag.x1 = p.x
      drag.y1 = p.y
      const nb = normRect(drag)
      if (snapOn && !noSnap && !snapConnected(nb, ['l', 'r', 't', 'b'])) {
        snapEdges(nb, ['l', 'r', 't', 'b'])
      }
      drag.snap = nb
    } else if (drag.mode === 'move') {
      const b = boxes[drag.i]
      b.x = drag.orig.x + (p.x - drag.x0)
      b.y = drag.orig.y + (p.y - drag.y0)
      markEdited(drag.i)
    } else {
      const b = boxes[drag.i]
      const o = drag.orig
      const m = drag.mode
      const x1 = o.x + o.w
      const y1 = o.y + o.h
      if (m.includes('w')) {
        b.x = Math.min(p.x, x1 - 10)
        b.w = x1 - b.x
      }
      if (m.includes('n')) {
        b.y = Math.min(p.y, y1 - 10)
        b.h = y1 - b.y
      }
      if (m.includes('e')) b.w = Math.max(10, p.x - o.x)
      if (m.includes('s')) b.h = Math.max(10, p.y - o.y)
      if (!noSnap && snapOn) {
        const e = []
        if (m.includes('w')) e.push('l')
        if (m.includes('e')) e.push('r')
        if (m.includes('n')) e.push('t')
        if (m.includes('s')) e.push('b')
        if (!snapConnected(b, e, drag.orig)) snapEdges(b, e)
      }
      markEdited(drag.i)
    }
    draw()
  }

  function mouseup() {
    if (panning) {
      panning = null
      return
    }
    if (!drag) return
    if (drag.mode === 'new') {
      const r = snapOn && drag.snap ? { ...drag.snap } : normRect(drag)
      if (drag.moved && r.w > 12 && r.h > 12) {
        boxes = [...boxes, { ...r, edited: true, source: 'manual' }]
        sel = boxes.length - 1
        const idx = sel
        drag = null
        draw()
        onchange('draw', idx)
        return
      }
    }
    const changed = drag.moved
    const idx = drag.i ?? -1
    drag = null
    draw()
    if (changed) onchange(drag?.mode || 'edit', idx)
  }

  function onWheel(ev) {
    if (!sw) return
    ev.preventDefault()
    if (ev.altKey || ev.ctrlKey || ev.metaKey) {
      const d = Math.max(-140, Math.min(140, ev.deltaY))
      setZoom(zoomVal * Math.exp(-d * 0.0017), ev.clientX, ev.clientY)
      return
    }
    const dx = ev.shiftKey ? ev.deltaY || ev.deltaX : ev.deltaX
    const dy = ev.shiftKey ? 0 : ev.deltaY
    panX -= dx
    panY -= dy
    clampPan()
    applyTransform()
  }

  function keydown(ev) {
    if (ev.code === 'Space' && !ev.target?.tagName?.match(/INPUT|TEXTAREA/)) spaceDown = true
  }
  function keyup(ev) {
    if (ev.code === 'Space') spaceDown = false
  }

  $effect(() => {
    if (!stageEl) return
    stageEl.addEventListener('wheel', onWheel, { passive: false })
    const observer = new ResizeObserver(() => layout())
    observer.observe(stageEl)
    return () => {
      stageEl.removeEventListener('wheel', onWheel)
      observer.disconnect()
    }
  })

  $effect(() => {
    window.addEventListener('keydown', keydown)
    window.addEventListener('keyup', keyup)
    return () => {
      window.removeEventListener('keydown', keydown)
      window.removeEventListener('keyup', keyup)
    }
  })

  // 换页 / 图片变化 → 重新取尺寸与掩码（内部会写 sw/pan 等状态，必须 untrack）
  $effect(() => {
    void imageUrl
    if (!imgEl) return
    if (imgEl.complete && imgEl.naturalWidth) untrack(onImageLoad)
  })

  // 框或选中变化 → 重绘
  $effect(() => {
    void boxes
    void sel
    void dark
    void showRev
    void review
    untrack(draw)
  })
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div
  class="stage"
  class:dark
  tabindex="-1"
  bind:this={stageEl}
  onmousedown={mousedown}
  onmousemove={mousemove}
  onmouseup={mouseup}
  onmouseleave={mouseup}
  oncontextmenu={(e) => e.preventDefault()}
  role="application"
  aria-label="字框编辑区"
>
  <div class="wrap" bind:this={wrapEl}>
    {#if imageUrl}
      <img
        bind:this={imgEl}
        src={imageUrl}
        use:autoRetry={imageUrl}
        alt="扫描页"
        draggable="false"
        onload={onImageLoad}
      />
    {/if}
    <canvas bind:this={ovEl}></canvas>
  </div>
</div>
