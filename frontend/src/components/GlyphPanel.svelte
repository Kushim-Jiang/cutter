<script>
  import { api } from '../lib/api.js'
  import { autoRetry } from '../lib/retry.js'

  // 右侧窄栏里的「本页全部字」：只列切图网格，按 栏 → y 排。
  // 整页示意不在这里做——中间那个画布本身就是带编号的整页示意。
  let {
    boxes = [],
    pageWidth = 0,
    pageHeight = 0,
    sel = -1,
    review = [],
    showReview = true,
    bookId = '',
    pageId = '',
    columns = 1,
    onselect = () => {},
    onedit = () => {},
  } = $props()

  let gridEl = $state(null)
  let gridBox = $state({ w: 0, h: 0 })

  // 字格大小：0 = 按可用面积自动铺满；拖滑杆后用手动值（记在本地）
  const SIZE_KEY = 'cutter.glyphSize'
  let size = $state(Number(localStorage.getItem(SIZE_KEY)) || 0)
  const GAP = 4
  const PAD = 16
  const MIN_SIZE = 20
  const MAX_SIZE = 180

  $effect(() => {
    if (size > MIN_SIZE) localStorage.setItem(SIZE_KEY, String(size))
    else localStorage.removeItem(SIZE_KEY)
  })

  // `scrollbar-gutter: stable` 让 clientWidth 不随滚动条出现/消失变化，
  // 否则「列数变 → 出滚动条 → 宽度变 → 列数再变」就会一直闪。
  $effect(() => {
    if (!gridEl) return
    const ro = new ResizeObserver(() => {
      gridBox = { w: gridEl.clientWidth, h: gridEl.clientHeight }
    })
    ro.observe(gridEl)
    gridBox = { w: gridEl.clientWidth, h: gridEl.clientHeight }
    return () => ro.disconnect()
  })

  const colCount = $derived(Math.max(1, Math.round(columns) || 1))

  /** 栏归属：按 x 中心最大的 (栏数−1) 个空隙切开（与后端 align.columns_of 同一口径）。 */
  const colOf = $derived.by(() => {
    const centers = boxes.map((b, i) => [b.x + b.w / 2, i])
    if (colCount <= 1 || centers.length < 2) {
      return new Map(centers.map(([, i]) => [i, 0]))
    }
    const sorted = [...centers].sort((a, b) => a[0] - b[0])
    const gaps = []
    for (let i = 0; i < sorted.length - 1; i++) {
      gaps.push([sorted[i + 1][0] - sorted[i][0], i])
    }
    const cuts = gaps
      .sort((a, b) => b[0] - a[0])
      .slice(0, Math.min(colCount - 1, gaps.length))
      .map(([, i]) => sorted[i][0])
      .sort((a, b) => a - b)
    const map = new Map()
    for (const [cx, i] of centers) {
      let col = 0
      for (const cut of cuts) if (cx > cut) col++
      map.set(i, col)
    }
    return map
  })

  /** 阅读顺序：栏 → y → x。`i` 始终是原始下标（编号与画布一致）。 */
  const ordered = $derived.by(() =>
    boxes
      .map((b, i) => ({ b, i, col: colOf.get(i) ?? 0 }))
      .sort((p, q) => p.col - q.col || p.b.y - q.b.y || p.b.x - q.b.x)
  )

  const availW = $derived(Math.max(1, gridBox.w - PAD))
  const availH = $derived(Math.max(1, gridBox.h - PAD))

  /** 自动尺寸：一屏放下整页，且不超过 MAX_SIZE。 */
  const autoSize = $derived.by(() => {
    const n = Math.max(1, boxes.length)
    if (!gridBox.w || !gridBox.h) return 64
    let best = 0
    for (let cols = 1; cols <= n; cols++) {
      const rows = Math.ceil(n / cols)
      const candidate = Math.min(
        (availW - GAP * (cols - 1)) / cols,
        (availH - GAP * (rows - 1)) / rows
      )
      if (candidate > best) best = candidate
    }
    return Math.min(MAX_SIZE, Math.max(MIN_SIZE, Math.floor(best)))
  })

  const cellSize = $derived(size > MIN_SIZE ? size : autoSize)

  // 每格带一圈上下文，并在图上画出真正的切框边线，
  // 才看得出这一刀切得紧不紧、有没有偏。
  // 按短边取外扩量：又长又扁的框（页眉、夹注）才不会带进一堆空白
  const PAD_RATIO = 0.22
  const PAD_MIN = 6

  function frames(b) {
    const pad = Math.max(PAD_MIN, Math.round(Math.min(b.w, b.h) * PAD_RATIO))
    const x0 = Math.max(0, Math.round(b.x - pad))
    const y0 = Math.max(0, Math.round(b.y - pad))
    const x1 = Math.min(pageWidth || b.x + b.w + pad, Math.round(b.x + b.w + pad))
    const y1 = Math.min(pageHeight || b.y + b.h + pad, Math.round(b.y + b.h + pad))
    const w = Math.max(1, x1 - x0)
    const h = Math.max(1, y1 - y0)
    return {
      outer: { x: x0, y: y0, w, h },
      line: {
        left: ((b.x - x0) / w) * 100,
        top: ((b.y - y0) / h) * 100,
        width: (b.w / w) * 100,
        height: (b.h / h) * 100,
      },
    }
  }

  /** 图在方格里按原比例居中显示（不靠 object-fit，好让框线百分比对得上）。 */
  function shotStyle(outer) {
    const ar = outer.w / outer.h
    const w = Math.min(cellSize, cellSize * ar)
    return `width:${w}px;height:${w / ar}px`
  }
  const cols = $derived(Math.max(1, Math.floor((availW + GAP) / (cellSize + GAP)) || 1))
</script>

<div class="glyphs">
  <div class="glyphs-bar">
    <span class="muted">{boxes.length} 框 · {colCount} 栏</span>
    <span style="flex:1"></span>
    <label class="size" title="字大小（拖到最左＝按面积自动）">
      <input
        type="range"
        min={MIN_SIZE}
        max={MAX_SIZE}
        step="2"
        value={cellSize}
        oninput={(e) => (size = Number(e.currentTarget.value))}
      />
      <span class="zlabel">{cellSize}px</span>
    </label>
  </div>

  <div
    class="glyphs-grid"
    bind:this={gridEl}
    style="grid-template-columns: repeat({cols}, {cellSize}px); gap: {GAP}px"
  >
    {#each ordered as item (item.i)}
      {@const f = frames(item.b)}
      {@const src = api.cropUrl(bookId, pageId, f.outer, 240)}
      <button
        class="cell"
        class:sel={item.i === sel}
        class:rev={showReview && review.includes(item.i)}
        class:edited={item.b.edited}
        style="width: {cellSize}px; height: {cellSize}px"
        onclick={() => onselect(item.i)}
        ondblclick={() => onedit(item.i)}
        title={`#${item.i + 1}  x=${Math.round(item.b.x)} y=${Math.round(item.b.y)} w=${Math.round(item.b.w)} h=${Math.round(item.b.h)}`}
      >
        <span class="shot" style={shotStyle(f.outer)}>
          <img
            {src}
            use:autoRetry={src}
            alt={`第 ${item.i + 1} 个字`}
            loading="lazy"
            decoding="async"
          />
          <span
            class="frame"
            style="left:{f.line.left}%;top:{f.line.top}%;width:{f.line.width}%;height:{f.line.height}%"
          ></span>
        </span>
        <span class="no">{item.i + 1}</span>
      </button>
    {/each}
  </div>
</div>
