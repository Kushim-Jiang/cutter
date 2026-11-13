<script>
  import { api } from '../lib/api.js'

  let {
    imageUrl = '',
    pageWidth = 0,
    pageHeight = 0,
    boxes = [],
    sel = -1,
    review = [],
    showReview = true,
    bookId = '',
    pageId = '',
    label = '',
    onselect = () => {},
    onclose = () => {},
  } = $props()

  let canvasEl = $state(null)
  let img = null
  let ready = $state(false)
  let hovered = $state(-1)

  const VIEW_MAX = 520

  $effect(() => {
    if (!imageUrl) return
    const next = new Image()
    next.onload = () => {
      img = next
      ready = true
      draw()
    }
    next.src = imageUrl
    return () => {
      next.onload = null
    }
  })

  $effect(() => {
    void boxes
    void sel
    void review
    void showReview
    void ready
    draw()
  })

  function draw() {
    const c = canvasEl?.getContext('2d')
    if (!c || !img || !ready || !pageWidth) return
    const scale = Math.min(VIEW_MAX / pageWidth, VIEW_MAX / pageHeight)
    const w = Math.round(pageWidth * scale)
    const h = Math.round(pageHeight * scale)
    canvasEl.width = w
    canvasEl.height = h
    c.fillStyle = '#fff'
    c.fillRect(0, 0, w, h)
    c.drawImage(img, 0, 0, w, h)

    boxes.forEach((b, i) => {
      const isSel = i === sel
      const isRev = showReview && review.includes(i)
      c.lineWidth = isSel ? 2.5 : 1.2
      c.strokeStyle = isSel ? '#ff6b00' : isRev ? '#e03131' : b.edited ? '#e8710a' : '#12a150'
      c.strokeRect(b.x * scale + 0.5, b.y * scale + 0.5, b.w * scale, b.h * scale)
      if (isSel) {
        c.fillStyle = 'rgba(255,107,0,.12)'
        c.fillRect(b.x * scale, b.y * scale, b.w * scale, b.h * scale)
      }
      const fs = Math.max(9, Math.min(16, b.h * scale * 0.7))
      c.font = `600 ${fs}px system-ui, sans-serif`
      c.textAlign = 'center'
      c.textBaseline = 'middle'
      c.lineWidth = 3
      c.strokeStyle = 'rgba(255,255,255,.9)'
      c.strokeText(String(i + 1), (b.x + b.w / 2) * scale, (b.y + b.h / 2) * scale)
      c.fillStyle = isSel ? '#ff6b00' : '#0b6bcb'
      c.fillText(String(i + 1), (b.x + b.w / 2) * scale, (b.y + b.h / 2) * scale)
    })
  }

  function pick(index) {
    onselect(index)
    onclose()
  }
</script>

<div class="dialog-backdrop" onclick={onclose} role="presentation">
  <div
    class="dialog wide"
    onclick={(e) => e.stopPropagation()}
    onkeydown={(e) => e.stopPropagation()}
    role="dialog"
    tabindex="-1"
    aria-label="本页全部字"
  >
    <div class="dialog-head">
      <h3>本页全部字（{boxes.length} 个框）{label ? ` · ${label}` : ''}</h3>
      <button onclick={onclose}>关闭</button>
    </div>
    <div class="overview">
      <div class="overview-page">
        <canvas bind:this={canvasEl}></canvas>
        <div class="muted" style="font-size: 12px; margin-top: 6px;">
          整页示意：编号＝框序号；绿＝自动、橙＝改过、红＝待查
        </div>
      </div>
      <div class="overview-grid">
        {#each boxes as box, i (i)}
          <button
            class="cell"
            class:sel={i === sel}
            class:rev={showReview && review.includes(i)}
            class:edited={box.edited}
            onmouseenter={() => (hovered = i)}
            onmouseleave={() => (hovered = -1)}
            onclick={() => pick(i)}
            title={`#${i + 1}  x=${Math.round(box.x)} y=${Math.round(box.y)} w=${Math.round(box.w)} h=${Math.round(box.h)}`}
          >
            <img src={api.cropUrl(bookId, pageId, box, 160)} alt={`第 ${i + 1} 个字`} />
            <span class="no">{i + 1}</span>
          </button>
        {/each}
      </div>
    </div>
  </div>
</div>
