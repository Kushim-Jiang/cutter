<script>
  let { imageUrl = '', pageWidth = 0, pageHeight = 0, box = null, side = 0, max = 152 } = $props()

  let hostEl = $state(null)
  let canvasEl = $state(null)
  let img = null
  let imgReady = $state(false)
  let size = $state(132)

  // 跟着栏宽走，最多 max 见方——「当前框」只是拿来对一眼，不该占掉半栏高度
  $effect(() => {
    if (!hostEl) return
    const fit = () => {
      size = Math.max(80, Math.min(max, Math.round(hostEl.clientWidth)))
    }
    const ro = new ResizeObserver(fit)
    ro.observe(hostEl)
    fit()
    return () => ro.disconnect()
  })

  $effect(() => {
    if (!imageUrl) return
    const next = new Image()
    next.onload = () => {
      img = next
      imgReady = true
      draw()
    }
    next.src = imageUrl
    return () => {
      next.onload = null
    }
  })

  $effect(() => {
    void box
    void imgReady
    void size
    draw()
  })

  function draw() {
    const c = canvasEl?.getContext('2d')
    if (!c) return
    const S = size
    c.fillStyle = '#fff'
    c.fillRect(0, 0, S, S)
    if (!box || !img || !imgReady || !pageWidth || !pageHeight) return

    const out = side || Math.max(Math.max(box.w, box.h) * 1.3, box.h * 2)
    const cx = box.x + box.w / 2
    const cy = box.y + box.h / 2
    const sx = ((cx - out / 2) / pageWidth) * img.naturalWidth
    const sy = ((cy - out / 2) / pageHeight) * img.naturalHeight
    const ss = (out / pageWidth) * img.naturalWidth

    c.imageSmoothingEnabled = true
    c.drawImage(img, sx, sy, ss, ss, 0, 0, S, S)

    // 框外涂白，只留框内的墨迹
    const padL = ((box.x - (cx - out / 2)) / out) * S
    const padT = ((box.y - (cy - out / 2)) / out) * S
    const padW = (box.w / out) * S
    const padH = (box.h / out) * S
    c.fillStyle = '#fff'
    c.fillRect(0, 0, S, padT)
    c.fillRect(0, padT + padH, S, S - padT - padH)
    c.fillRect(0, 0, padL, S)
    c.fillRect(padL + padW, 0, S - padL - padW, S)

    // 最外一圈白边
    const ring = 0.06 * S
    c.fillRect(0, 0, S, ring)
    c.fillRect(0, S - ring, S, ring)
    c.fillRect(0, 0, ring, S)
    c.fillRect(S - ring, 0, ring, S)

    c.strokeStyle = '#12a150'
    c.lineWidth = 2
    c.strokeRect(padL, padT, padW, padH)
  }
</script>

<div class="pv-host" bind:this={hostEl}>
  <canvas bind:this={canvasEl} width={size} height={size} class="preview"></canvas>
</div>
