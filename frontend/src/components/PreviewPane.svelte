<script>
  let { imageUrl = '', pageWidth = 0, pageHeight = 0, box = null, side = 0 } = $props()

  let canvasEl = $state(null)
  let img = null
  let imgReady = $state(false)

  const SIZE = 300

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
    draw()
  })

  function draw() {
    const c = canvasEl?.getContext('2d')
    if (!c) return
    c.fillStyle = '#fff'
    c.fillRect(0, 0, SIZE, SIZE)
    if (!box || !img || !imgReady || !pageWidth || !pageHeight) return

    const out = side || Math.max(Math.max(box.w, box.h) * 1.3, box.h * 2)
    const cx = box.x + box.w / 2
    const cy = box.y + box.h / 2
    const sx = ((cx - out / 2) / pageWidth) * img.naturalWidth
    const sy = ((cy - out / 2) / pageHeight) * img.naturalHeight
    const ss = (out / pageWidth) * img.naturalWidth

    c.imageSmoothingEnabled = true
    c.drawImage(img, sx, sy, ss, ss, 0, 0, SIZE, SIZE)

    // 框外涂白，只留框内的墨迹
    const padL = ((box.x - (cx - out / 2)) / out) * SIZE
    const padT = ((box.y - (cy - out / 2)) / out) * SIZE
    const padW = (box.w / out) * SIZE
    const padH = (box.h / out) * SIZE
    c.fillStyle = '#fff'
    c.fillRect(0, 0, SIZE, padT)
    c.fillRect(0, padT + padH, SIZE, SIZE - padT - padH)
    c.fillRect(0, 0, padL, SIZE)
    c.fillRect(padL + padW, 0, SIZE - padL - padW, SIZE)

    // 最外一圈白边
    const ring = 0.06 * SIZE
    c.fillRect(0, 0, SIZE, ring)
    c.fillRect(0, SIZE - ring, SIZE, ring)
    c.fillRect(0, 0, ring, SIZE)
    c.fillRect(SIZE - ring, 0, ring, SIZE)

    c.strokeStyle = '#12a150'
    c.lineWidth = 2
    c.strokeRect(padL, padT, padW, padH)
  }
</script>

<canvas bind:this={canvasEl} width={SIZE} height={SIZE} class="preview"></canvas>
