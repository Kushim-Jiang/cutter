<script>
  import { onMount } from 'svelte'
  import { api } from './lib/api.js'
  import CheckModal from './components/CheckModal.svelte'
  import AlignTable from './components/AlignTable.svelte'
  import GlyphPanel from './components/GlyphPanel.svelte'
  import SheetView from './components/SheetView.svelte'
  import HistoryModal from './components/HistoryModal.svelte'
  import PreviewPane from './components/PreviewPane.svelte'
  import SettingsDialog from './components/SettingsDialog.svelte'
  import Stage from './components/Stage.svelte'

  let books = $state([])
  let bookId = $state('')
  let pages = $state([])
  let pageId = $state('')
  let meta = $state(null)
  let params = $state({})

  let boxes = $state([])
  let extra = $state({})
  let sel = $state(-1)
  let review = $state([])
  let side = $derived(meta?.side || 0)

  let snapOn = $state(true)
  let dark = $state(false)
  let showRev = $state(false)
  let railShown = $state(localStorage.getItem('cutter.rail') !== '0')
  let gap = $state(2)
  let zoomPct = $state(100)
  let gotoText = $state('')

  let pending = $state({}) // page -> boxes（还没写盘）
  let touched = false      // 当前页是否改过
  let savedIds = $state(new Set())
  let doneIds = $state(new Set())
  let suspectIds = $state(new Set())
  let loading = $state(false)
  let busy = $state(false)
  let error = $state('')
  let message = $state('')
  let settings = $state({})
  let settingsOpen = $state(false)
  let importInput = $state(null)

  // ── 自动落盘 ────────────────────────────────────────────
  // 停手 AUTOSAVE_DELAY 毫秒就写盘；同一页的写串行（单飞），避免请求连太紧
  // 时旧响应盖掉新改动。每次写带 base_rev，服务端版本对不上会回 409。
  const AUTOSAVE_DELAY = 900
  let saveState = $state('saved') // saved | pending | saving | error | conflict
  let savedAt = $state('')
  const saveTimers = new Map() // pageId -> timeout
  const saveChains = new Map() // pageId -> Promise（同页单飞）
  const dirtySeq = new Map() // pageId -> 改动序号（判断保存期间有没有新改动）
  const pageRevs = new Map() // pageId -> 服务端版本号
  const pageSizes = new Map() // pageId -> { width, height }
  const conflicts = new Set() // 409 过的页：暂停自动写，等用户决定

  let moreOpen = $state(false)
  let alignOpen = $state(false)
  let sheetOpen = $state(false)
  let checkOpen = $state(false)
  let checkReport = $state(null)
  let checkLoading = $state(false)
  let historyOpen = $state(false)
  let historyItems = $state([])
  let historyUpstream = $state([])
  let historyLoading = $state(false)

  let stage = $state(null)
  let epoch = 0

  // 撤销 / 重做：只动内存，不碰磁盘（与「回车才写盘」一致）
  const UNDO_LIMIT = 100
  let undoStack = $state([])
  let redoStack = $state([])
  let pendingUndo = null // 一次拖动开始前的快照；真的改了才入栈

  // 每本书的栏数（版面是固定的，靠阈值猜会误判：康熙某页只有 4 框，
  // 栏间空隙只有 1.27 倍字宽，当成 3 栏就错了）
  const BOOK_COLUMNS = { zihai: 3, hzh: 3, kx: 2, hd: 2 }

  const book = $derived(books.find((b) => b.id === bookId) || null)
  const volumes = $derived(book?.status?.volumes || [])
  const currentVolume = $derived(
    pageId.includes(':') ? pageId.split(':')[0] : (volumes[0]?.vol ?? '')
  )
  const pageIndex = $derived(pages.findIndex((p) => p.id === pageId))
  const printedPage = $derived(pages.find((p) => p.id === pageId)?.printed_page || meta?.printed_page || 1)
  const imageUrl = $derived(
    meta && pageId ? api.previewUrl(bookId, pageId, settings.preview_width || 2200) : ''
  )
  const dirtyCount = $derived(Object.keys(pending).length)
  const box = $derived(sel >= 0 ? boxes[sel] : null)
  const reviewSet = $derived(new Set(review))

  onMount(async () => {
    await Promise.all([reloadSettings(), reloadBooks()])
    const preferred =
      books.find((b) => b.id === 'zihai' && b.status.available) ||
      books.find((b) => b.status.available) ||
      books[0]
    if (preferred) await selectBook(preferred.id)
  })

  async function reloadSettings() {
    try {
      settings = await api.settings()
      gap = Number(settings.gap ?? 2)
    } catch (err) {
      error = err.message
    }
  }

  async function reloadBooks() {
    try {
      books = (await api.books()).books
    } catch (err) {
      error = err.message
    }
  }

  async function selectBook(id) {
    if (!id) return
    await flushPending()
    const token = ++epoch
    bookId = id
    pageId = ''
    meta = null
    boxes = []
    sel = -1
    pending = {}
    touched = false
    savedIds = new Set()
    doneIds = new Set()
    suspectIds = new Set()
    const current = books.find((b) => b.id === id)
    params = { ...(current?.defaults || {}) }
    try {
      const result = await api.pages(id)
      if (token !== epoch) return
      pages = result.pages
      if (pages.length) {
        // 有上游定稿就直接落到第一个定稿页（康熙第 1 册是总目，没有字头）
        const first = pages.find((p) => p.seeded) || pages[0]
        await openPage(first, token)
        if (token === epoch) await refreshProgress()
      }
    } catch (err) {
      if (token === epoch) error = err.message
    }
  }

  async function refreshProgress() {
    try {
      const progress = await api.progress(bookId)
      savedIds = new Set(progress.saved_pages || [])
      doneIds = new Set(progress.done_pages || [])
      suspectIds = new Set(progress.suspect_pages || [])
    } catch {
      /* 进度不是关键路径 */
    }
  }

  async function openAt(id, index = -1) {
    alignOpen = false
    const target = pages.find((p) => p.id === id)
    if (!target) {
      error = `找不到页面 ${id}`
      return
    }
    if (target.id !== pageId) await openPage(target)
    if (index >= 0 && index < boxes.length) {
      sel = index
      stage?.focusBox(index)
    }
  }

  async function openPage(page, inheritedToken = null) {
    if (!page || page.id === pageId) return
    // 切页前先把当前页写掉，别把改动留在内存里赌窗口不关
    if (pageId && pending[pageId]) await flushPage(pageId)
    touched = false
    const token = inheritedToken ?? ++epoch
    pageId = page.id
    meta = null
    boxes = []
    extra = {}
    sel = -1
    review = []
    undoStack = []
    redoStack = []
    pendingUndo = null
    error = ''
    message = ''
    loading = true
    try {
      const info = await api.meta(bookId, page.id)
      if (token !== epoch) return
      meta = info
      pageSizes.set(page.id, { width: info.width || 0, height: info.height || 0 })
      let data
      if (pending[page.id]) {
        data = { boxes: pending[page.id], extra: {} }
      } else {
        const saved = await api.boxes(bookId, page.id)
        if (token !== epoch) return
        if (saved.saved) {
          data = saved
        } else {
          // 没存过：先按书源自动检测一份（不写盘），与校对台缓存的自动框一致
          const detected = await api.detect(bookId, page.id, params, false)
          if (token !== epoch) return
          data = { boxes: detected.boxes, extra: detected.meta }
        }
      }
      pageRevs.set(page.id, data.rev ?? 0)
      boxes = (data.boxes || []).map((b) => ({ ...b, edited: b.source === 'manual' }))
      review = data.extra?.review || []
      extra = data.extra || {}
      if (data.origin === 'seed' || data.extra?.seed) {
        message = `已载入上游定稿：${boxes.length} 个框`
      }
    } catch (err) {
      if (token !== epoch) return
      error = err.message
      meta = null
      boxes = []
    } finally {
      if (token === epoch) loading = false
    }
  }

  function markDirty() {
    if (!pageId) return
    touched = true
    pending = { ...pending, [pageId]: boxes }
    dirtySeq.set(pageId, (dirtySeq.get(pageId) || 0) + 1)
    scheduleSave(pageId)
  }

  // ── 撤销 / 重做 ─────────────────────────────────────────
  function snapState() {
    return JSON.stringify({ boxes, review, sel })
  }

  function pushUndo(state = null) {
    undoStack.push(state ?? snapState())
    if (undoStack.length > UNDO_LIMIT) undoStack.shift()
    redoStack = []
  }

  /** Stage 开始一次拖动前调用：先记住快照，真改了才入栈。 */
  function beginChange() {
    if (!pageId) return
    pendingUndo = snapState()
  }

  /** Stage 确认改动后调用。 */
  function commitChange() {
    if (pendingUndo) {
      pushUndo(pendingUndo)
      pendingUndo = null
    }
    markDirty()
  }

  function applyState(raw) {
    const data = JSON.parse(raw)
    boxes = data.boxes || []
    review = data.review || []
    sel = Math.min(data.sel ?? -1, boxes.length - 1)
    markDirty()
  }

  function undo() {
    if (!undoStack.length) {
      message = '没有可撤销的操作'
      return
    }
    redoStack.push(snapState())
    applyState(undoStack.pop())
    message = '已撤销'
  }

  function redo() {
    if (!redoStack.length) {
      message = '没有可重做的操作'
      return
    }
    undoStack.push(snapState())
    applyState(redoStack.pop())
    message = '已重做'
  }

  async function savePage(id, boxList) {
    const info = id === pageId ? meta : null
    await api.saveBoxes(bookId, id, {
      boxes: boxList,
      width: info?.width || 0,
      height: info?.height || 0,
      extra: id === pageId ? extra : null,
      state: {},
    })
    savedIds.add(id)
    savedIds = new Set(savedIds)
    const next = { ...pending }
    delete next[id]
    pending = next
  }

  function scheduleSave(id) {
    const timer = saveTimers.get(id)
    if (timer) clearTimeout(timer)
    saveState = 'pending'
    saveTimers.set(
      id,
      setTimeout(() => {
        saveTimers.delete(id)
        void flushPage(id)
      }, AUTOSAVE_DELAY)
    )
  }

  /** 立刻写某一页。同页串行：后来的写排在前一次之后，不会并发。 */
  function flushPage(id, options = {}) {
    const timer = saveTimers.get(id)
    if (timer) {
      clearTimeout(timer)
      saveTimers.delete(id)
    }
    const tail = saveChains.get(id) || Promise.resolve(true)
    const run = tail.catch(() => true).then(() => writePage(id, options))
    saveChains.set(id, run)
    return run
  }

  async function writePage(id, { manual = false } = {}) {
    // 冲突时拒绝写；没有待写改动不算失败——「标记完成」这类调用要能继续走下去
    if (conflicts.has(id)) return false
    const list = pending[id]
    if (!list) return true
    const seq = dirtySeq.get(id) || 0
    const size = pageSizes.get(id) || { width: 0, height: 0 }
    saveState = 'saving'
    try {
      const res = await api.saveBoxes(bookId, id, {
        boxes: list.map((b) => ({
          ...b,
          x: Math.round(b.x),
          y: Math.round(b.y),
          w: Math.max(1, Math.round(b.w)),
          h: Math.max(1, Math.round(b.h)),
        })),
        width: size.width,
        height: size.height,
        extra: id === pageId ? extra : null,
        state: {},
        autosave: !manual,
        base_rev: pageRevs.has(id) ? pageRevs.get(id) : null,
      })
      pageRevs.set(id, res.rev)
      if (res.saved_at) savedAt = res.saved_at
      savedIds.add(id)
      savedIds = new Set(savedIds)
      if ((dirtySeq.get(id) || 0) === seq) {
        const next = { ...pending }
        delete next[id]
        pending = next
        saveState = 'saved'
      } else {
        scheduleSave(id) // 写盘期间又改了，接着写
      }
      return true
    } catch (err) {
      if (err.status === 409) {
        conflicts.add(id)
        saveState = 'conflict'
        error = `${pages.find((p) => p.id === id)?.label || id} 已被其他窗口修改，自动写盘已暂停。` +
          '本地改动还在（可先「导出修正 JSON」），确认后点「重新载入」或「覆盖保存」。'
      } else if (err.status >= 400 && err.status < 500) {
        // 请求本身有问题，重试多少次都一样：停下来等人看
        saveState = 'error'
        error = `写盘被拒（${err.status}）：${err.message}`
      } else {
        saveState = 'error'
        error = `写盘失败：${err.message}（已排队重试）`
        scheduleSave(id)
      }
      return false
    }
  }

  async function flushPending() {
    for (const id of Object.keys(pending)) await flushPage(id)
  }

  /** 冲突后强制覆盖：清掉版本基准再写一次。 */
  async function forceSave() {
    const ids = [...conflicts]
    for (const id of ids) {
      conflicts.delete(id)
      const remote = await api.boxes(bookId, id)
      pageRevs.set(id, remote.rev ?? 0)
    }
    error = ''
    saveState = 'pending'
    for (const id of ids) await flushPage(id, { manual: true })
    if (!conflicts.size) message = '已覆盖保存'
  }

  /** 冲突后丢弃本地改动，改用服务端版本。 */
  async function reloadPage() {
    const id = pageId
    if (!id) return
    conflicts.delete(id)
    const next = { ...pending }
    delete next[id]
    pending = next
    error = ''
    saveState = 'saved'
    const saved = await api.boxes(bookId, id)
    pageRevs.set(id, saved.rev ?? 0)
    boxes = (saved.boxes || []).map((b) => ({ ...b, edited: b.source === 'manual' }))
    review = saved.extra?.review || []
    extra = saved.extra || {}
    undoStack = []
    redoStack = []
    sel = -1
    message = '已按服务端版本重新载入'
  }

  async function saveAndNext() {
    if (!pageId) return
    busy = true
    const id = pageId
    try {
      if (!(await flushPage(id, { manual: true }))) {
        busy = false
        return
      }
      const res = await api.setState(bookId, id, { done: true })
      pageRevs.set(id, res.rev)
      doneIds.add(id)
      doneIds = new Set(doneIds)
      message = '已写盘并标记完成'
      await nextIn('undone')
    } catch (err) {
      error = err.message
    } finally {
      busy = false
    }
  }

  async function markDone() {
    if (!pageId) return
    busy = true
    const id = pageId
    try {
      if (!(await flushPage(id, { manual: true }))) {
        busy = false
        return
      }
      const res = await api.setState(bookId, id, { done: true })
      pageRevs.set(id, res.rev)
      doneIds.add(id)
      doneIds = new Set(doneIds)
      message = '本页已完成并已写盘'
    } catch (err) {
      error = err.message
    } finally {
      busy = false
    }
  }

  async function resetPage() {
    if (!pageId) return
    busy = true
    error = ''
    const id = pageId
    try {
      conflicts.delete(id)
      const result = await api.detect(bookId, id, params, true)
      pushUndo()
      boxes = result.boxes
      review = result.meta.review || []
      extra = result.meta || {}
      sel = -1
      touched = false
      if (result.rev != null) pageRevs.set(id, result.rev)
      const next = { ...pending }
      delete next[id]
      pending = next
      saveState = 'saved'
      savedIds.add(id)
      savedIds = new Set(savedIds)
      message = `本页已还原：${result.count} 个框`
    } catch (err) {
      error = err.message
    } finally {
      busy = false
    }
  }

  // ── 框操作 ─────────────────────────────────────────────
  function refitBox(index, record = true) {
    if (index < 0 || !boxes[index] || !stage) return
    if (record) pushUndo()
    const b = { ...boxes[index] }
    stage.refit(b)
    const next = boxes.slice()
    next[index] = { ...b, edited: true, source: 'manual' }
    boxes = next
    sel = index
    markDirty()
  }

  function refitSelected() {
    refitBox(sel)
  }

  function refitAll() {
    if (!boxes.length || !stage) return
    pushUndo()
    boxes = boxes.map((b) => {
      const copy = { ...b }
      stage.refit(copy)
      return { ...copy, edited: true, source: 'manual' }
    })
    markDirty()
    message = '本页全部重贴完成'
  }

  /** 本页全部框按「连通块」吸附：一个框只框住一个连通字（同一字的几笔算一块）。 */
  function snapConnectedAll() {
    if (!boxes.length || !stage) return
    pushUndo()
    let n = 0
    boxes = boxes.map((b) => {
      const copy = { ...b }
      if (!stage.refitConnected(copy)) return b
      n++
      return { ...copy, edited: true, source: 'manual' }
    })
    markDirty()
    message = `连通吸附：${n}/${boxes.length} 个框已重贴` + (n < boxes.length ? '（其余框里没有连通块）' : '')
  }

  function deleteBox() {
    if (sel < 0) return
    pushUndo()
    const next = boxes.slice()
    next.splice(sel, 1)
    boxes = next
    review = review.filter((i) => i !== sel).map((i) => (i > sel ? i - 1 : i))
    sel = -1
    markDirty()
  }

  function addBox() {
    pushUndo()
    const base = box ? { ...box } : { x: 100, y: 100, w: 120, h: 120 }
    boxes = [
      ...boxes,
      {
        x: Math.round(base.x + 20),
        y: Math.round(base.y + 20),
        w: Math.round(base.w),
        h: Math.round(base.h),
        edited: true,
        source: 'manual',
      },
    ]
    sel = boxes.length - 1
    markDirty()
    stage?.focusBox(sel)
  }

  function nudge(dx, dy) {
    if (sel < 0 || !boxes[sel]) return
    pushUndo()
    const next = boxes.slice()
    const b = { ...next[sel] }
    b.x += dx
    b.y += dy
    b.edited = true
    next[sel] = b
    boxes = next
    markDirty()
  }

  function cycleBox(step = 1) {
    if (!boxes.length) return
    sel = (sel + step + boxes.length) % boxes.length
    stage?.focusBox(sel)
  }

  function nextReview() {
    if (!review.length) {
      message = '本页没有待查框'
      return
    }
    const order = [...review].sort((a, b) => a - b)
    const after = order.find((i) => i > sel)
    const idx = after ?? order[0]
    sel = idx
    stage?.focusBox(idx)
  }

  // ── 导航 ───────────────────────────────────────────────
  function goRelative(delta) {
    const next = pageIndex + delta
    if (next >= 0 && next < pages.length) openPage(pages[next])
  }

  async function jump() {
    const text = String(gotoText).trim()
    if (!text) return
    const vol = currentVolume
    const inVol = (p) => !vol || p.volume === vol
    const numeric = /^\d+$/.test(text)
    // 只在本卷里找：跨卷同号容易跳错册，换册用上面的分卷下拉
    const found =
      pages.find((p) => inVol(p) && String(p.printed_page) === text) ||
      pages.find((p) => p.id === text) ||
      (numeric ? undefined : pages.find((p) => inVol(p) && `${p.label}`.includes(text)))
    if (found) {
      gotoText = ''
      await openPage(found)
    } else {
      const inVolume = pages.filter(inVol)
      const nums = inVolume.map((p) => Number(p.printed_page)).filter((n) => Number.isFinite(n))
      const span = nums.length ? `，本册只开放第 ${Math.min(...nums)}–${Math.max(...nums)} 页` : ''
      error = numeric ? `没有第 ${text} 页${span}` : `找不到「${text}」`
    }
  }

  async function nextIn(mode) {
    try {
      const result = await api.nav(bookId, pageId, mode)
      if (!result.found) {
        message = mode === 'review' ? '没有可疑页了' : '全部完成'
        return
      }
      const target = pages.find((p) => p.id === result.page)
      if (target) await openPage(target)
    } catch (err) {
      error = err.message
    }
  }

  function switchVolume(vol) {
    const index = pages.findIndex((p) => p.volume === vol)
    if (index >= 0) {
      const firstSeeded = pages.slice(index).find((p) => p.seeded && p.volume === vol)
      openPage(firstSeeded || pages[index])
    }
  }

  // ── 导入导出 ───────────────────────────────────────────
  function exportJson() {
    const payload = {
      book: bookId,
      pages: Object.keys(pending).length
        ? pending
        : pageId
          ? { [pageId]: boxes }
          : {},
    }
    const blob = new Blob([JSON.stringify(payload, null, 1)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `cutter_${bookId}_${pageId || 'all'}.json`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  async function importJson(event) {
    const file = event.currentTarget.files?.[0]
    if (!file) return
    try {
      const data = JSON.parse(await file.text())
      const list = data.boxes || (pageId && data.pages?.[pageId]) || []
      if (!Array.isArray(list)) throw new Error('JSON 里没有 boxes')
      pushUndo()
      boxes = list.map((b) => ({ x: b.x, y: b.y, w: b.w, h: b.h, edited: true, source: 'manual' }))
      sel = -1
      review = []
      markDirty()
      message = `已导入 ${boxes.length} 个框，回车写盘`
    } catch (err) {
      error = `导入失败：${err.message}`
    } finally {
      event.currentTarget.value = ''
    }
  }

  // ── 本页工具：合法性检查 / 编辑历史 ─────────────────────
  async function runCheck() {
    if (!pageId) return
    checkOpen = true
    checkLoading = true
    try {
      checkReport = await api.check(bookId, pageId)
    } catch (err) {
      error = err.message
      checkReport = null
    } finally {
      checkLoading = false
    }
  }

  async function openHistory() {
    if (!pageId) return
    historyOpen = true
    historyLoading = true
    try {
      const data = await api.history(bookId, pageId)
      historyItems = data.items || []
      historyUpstream = data.upstream || []
    } catch (err) {
      error = err.message
      historyItems = []
      historyUpstream = []
    } finally {
      historyLoading = false
    }
  }

  async function restoreSnapshot(snapshotId) {
    try {
      const data = await api.restoreHistory(bookId, pageId, snapshotId)
      pushUndo()
      boxes = (data.boxes || []).map((b) => ({ ...b, edited: b.source === 'manual' }))
      sel = -1
      review = []
      markDirty()
      historyOpen = false
      message = `已恢复到 ${data.time}（${data.count} 个框），按回车写盘`
    } catch (err) {
      error = err.message
    }
  }

  async function saveSettings(draft) {
    try {
      settings = await api.saveSettings({
        export_dir: draft.export_dir,
        preview_width: Number(draft.preview_width) || 2200,
        use_seed: !!draft.use_seed,
        books: draft.books,
      })
      settingsOpen = false
      await reloadBooks()
      if (bookId) await selectBook(bookId)
    } catch (err) {
      error = err.message
    }
  }

  // ── 键盘（与校对台一致） ───────────────────────────────
  let shiftOnly = false

  function keydown(event) {
    const tag = event.target?.tagName?.toLowerCase()
    if (tag === 'input' || tag === 'select' || tag === 'textarea') return
    if (!pageId) return
    if (event.key === 'Shift') shiftOnly = true
    else shiftOnly = false
    const step = event.shiftKey ? 10 : 1
    const mod = event.ctrlKey || event.metaKey
    if (mod && event.key.toLowerCase() === 'z') {
      event.preventDefault()
      if (event.shiftKey) redo()
      else undo()
    } else if (mod && event.key.toLowerCase() === 'y') {
      event.preventDefault()
      redo()
    } else if (mod && event.key.toLowerCase() === 's') {
      event.preventDefault()
      flushPending()
    } else if (event.key === 'Enter') {
      event.preventDefault()
      saveAndNext()
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault()
      if (sel >= 0) nudge(-step, 0)
      else goRelative(-1)
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      if (sel >= 0) nudge(step, 0)
      else goRelative(1)
    } else if (event.key === 'F2') {
      event.preventDefault()
      moreOpen = !moreOpen
    } else if (event.key === 'F3') {
      event.preventDefault()
      moreOpen = false
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      nudge(0, -step)
    } else if (event.key === 'ArrowDown') {
      event.preventDefault()
      nudge(0, step)
    } else if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault()
      deleteBox()
    } else if (event.key === 'Tab') {
      event.preventDefault()
      cycleBox(event.shiftKey ? -1 : 1)
    } else if (event.key.toLowerCase() === 'f') {
      event.preventDefault()
      refitSelected()
    } else if (event.key === '0') {
      event.preventDefault()
      stage?.fit()
    } else if (event.key === '1') {
      event.preventDefault()
      stage?.setZoom(1)
    } else if (event.key.toLowerCase() === 'd') {
      event.preventDefault()
      dark = !dark
    } else if (event.key.toLowerCase() === 'a') {
      event.preventDefault()
      refitAll()
    }
  }

  function keyup(event) {
    if (event.key === 'Shift') {
      if (shiftOnly) snapConnectedAll()
      shiftOnly = false
    }
  }

  $effect(() => {
    window.addEventListener('keydown', keydown)
    window.addEventListener('keyup', keyup)
    return () => {
      window.removeEventListener('keydown', keydown)
      window.removeEventListener('keyup', keyup)
    }
  })

  $effect(() => {
    document.body.classList.toggle('dark', dark)
  })

  $effect(() => {
    localStorage.setItem('cutter.rail', railShown ? '1' : '0')
  })

  // 切到后台（切标签、锁屏）时立刻把改动写掉
  $effect(() => {
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') void flushPending()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  })
</script>

<svelte:window
  onbeforeunload={(event) => {
    if (!dirtyCount) return
    // 关窗口/刷新前用 keepalive 把没写盘的页塞出去；写失败过才拦一下
    for (const id of Object.keys(pending)) {
      const size = pageSizes.get(id) || { width: 0, height: 0 }
      api.saveBoxesKeepalive(bookId, id, {
        boxes: pending[id],
        width: size.width,
        height: size.height,
        extra: id === pageId ? extra : null,
        state: {},
        autosave: true,
        base_rev: pageRevs.has(id) ? pageRevs.get(id) : null,
      })
    }
    if (saveState === 'error' || saveState === 'conflict') {
      event.preventDefault()
      event.returnValue = ''
    }
  }}
/>

<div class="app" class:rail-hidden={!railShown}>
  <nav class="rail">
    <div class="rail-head">
      <b>字头框手动校对</b>
      <button class="icon" onclick={() => (railShown = false)} title="收起左栏">«</button>
    </div>

    <section>
      <h4>书源</h4>
      <select class="books" value={bookId} onchange={(e) => selectBook(e.currentTarget.value)}>
        {#each books as item (item.id)}
          <option value={item.id}>{item.name}{item.status.available ? '' : '（未配置）'}</option>
        {/each}
      </select>
      {#if volumes.length > 1}
        <select class="vol" value={currentVolume} onchange={(e) => switchVolume(e.currentTarget.value)}
                title="切换分卷">
          {#each volumes as v (v.vol)}
            <option value={v.vol}>{v.label || v.title}</option>
          {/each}
        </select>
      {/if}
    </section>

    <section>
      <h4>翻页</h4>
      <div class="navrow">
        <button onclick={() => goRelative(-1)} disabled={pageIndex <= 0} title="上一页">◀</button>
        <span class="pglabel">{meta?.label || `${pageIndex + 1} / ${pages.length}`}</span>
        <button onclick={() => goRelative(1)} disabled={pageIndex < 0 || pageIndex >= pages.length - 1}
                title="下一页">▶</button>
      </div>
      <div class="navrow2">
        <input
          type="number"
          class="goto"
          placeholder="页码"
          min="1"
          bind:value={gotoText}
          onkeydown={(e) => e.key === 'Enter' && jump()}
        />
        <button onclick={jump}>跳转</button>
      </div>
      <button onclick={() => nextIn('undone')} title="跳到下一个还没标记完成的页">下一未完成</button>
      <div class="grid2">
        <button onclick={() => nextIn('review')} title="跳到下一个有可疑框的页">下一可疑页</button>
        <button onclick={nextReview} title="在本页内跳到下一个标红的待查框">下一待查框</button>
      </div>
      <button class="primary" onclick={markDone} title="标记本页已校对完，并跳到下一未完成页">
        ✓ 本页完成 →
      </button>
    </section>

    <section>
      <h4>视图</h4>
      <label class="zoom">
        缩放
        <input
          type="range"
          min="0.2"
          max="8"
          step="0.05"
          value={zoomPct / 100}
          oninput={(e) => stage?.setZoom(Number(e.currentTarget.value) || 1)}
        />
        <span class="zlabel">{Math.round(zoomPct)}%</span>
      </label>
      <button onclick={() => stage?.fit()} title="整页适合窗口（按 0）">适合窗口</button>
      <div class="grid3">
        <button class:on={snapOn} onclick={() => (snapOn = !snapOn)} title="拖动时自动贴着墨迹">
          吸附{snapOn ? ' ✓' : ''}
        </button>
        <button class:on={dark} onclick={() => (dark = !dark)} title="黑夜模式（按 D）">
          黑夜{dark ? ' ✓' : ''}
        </button>
        <button class:on={showRev} onclick={() => (showRev = !showRev)} title="把可疑框标红（判据不完美，会误报）">
          标红{showRev ? ' ✓' : ''}
        </button>
      </div>
    </section>

    <section>
      <h4>编辑</h4>
      <div class="grid2">
        <button onclick={undo} disabled={!undoStack.length} title="撤销（Ctrl+Z）">↶ 撤销</button>
        <button onclick={redo} disabled={!redoStack.length} title="重做（Ctrl+Shift+Z / Ctrl+Y）">↷ 重做</button>
      </div>
      <button onclick={exportJson}>导出修正 JSON</button>
      <button onclick={() => importInput?.click()}>导入 JSON</button>
      <input
        type="file"
        accept=".json,application/json"
        style="display:none"
        bind:this={importInput}
        onchange={importJson}
      />
      <button class="danger" onclick={resetPage} title="丢掉本页改动，按当前检测参数重新检测">本页还原</button>
    </section>

    <section class="status">
      {#if saveState === 'conflict'}
        <span class="pill err" title="本页已被其他窗口改过，自动写盘已暂停">写盘冲突</span>
      {:else if saveState === 'error'}
        <span class="pill err" title="写盘失败，已排队重试">写盘失败</span>
      {:else if saveState === 'saving'}
        <span class="pill warn">写盘中…</span>
      {:else if saveState === 'pending'}
        <span class="pill warn">待写盘{dirtyCount > 1 ? `（${dirtyCount} 页）` : ''}</span>
      {:else}
        <span class="pill ok" title={savedAt ? `上次写盘 ${savedAt}` : '没有待写改动'}>已写盘</span>
      {/if}
      {#if meta?.seeded}
        <span
          class="pill ok"
          title={meta.seed_kind === 'predict'
            ? '本页只有本工具跑出来的预测框（还没人工校过）'
            : '本页用的是上游已切好的定稿数据'}
        >
          {meta.seed_kind === 'predict' ? '预测' : '定稿'}
        </span>
      {/if}
      <span class="prog" title="本页完成后会记在服务端">{doneIds.size}/{pages.length} 完成</span>
      <span class="stat">本页 {boxes.length} 个框</span>
    </section>

    {#if saveState === 'conflict'}
      <div class="grid2">
        <button onclick={forceSave} title="用本地改动覆盖服务端">覆盖保存</button>
        <button class="danger" onclick={reloadPage} title="丢弃本地改动，改用服务端版本">重新载入</button>
      </div>
    {/if}

    <div class="rail-foot">
      <button onclick={() => (settingsOpen = true)}>设置</button>
    </div>
  </nav>

  {#if !railShown}
    <button class="rail-show" onclick={() => (railShown = true)} title="展开左栏（栏内全部是原来的顶栏按钮）">
      »
    </button>
  {/if}

  <main>
    <div class="stage-wrap">
      <Stage
        bind:this={stage}
        {imageUrl}
        pageWidth={meta?.width || 0}
        pageHeight={meta?.height || 0}
        bind:boxes
        bind:sel
        {snapOn}
        {dark}
        {showRev}
        review={[...reviewSet]}
        bind:gap
        onbeforechange={beginChange}
        onchange={(kind) => {
          commitChange()
          if (snapOn && kind === 'draw' && sel >= 0 && boxes[sel]) refitBox(sel, false)
        }}
        onzoom={(z) => (zoomPct = z * 100)}
      />
      {#if loading}
        <div class="overlay-hint">检测 / 载入中…</div>
      {:else if !meta}
        <div class="overlay-hint">
          <strong>还没有可显示的页面</strong>
          <span>先在「设置」里配置书源（扫描 PDF 路径）</span>
        </div>
      {/if}
      {#if busy}<div class="overlay-hint small">处理中…</div>{/if}
    </div>

    <aside class="side">
      <div class="side-head">
        <div class="head-top">
          <div class="head-left">
            <h3>当前框</h3>
            <PreviewPane
              {imageUrl}
              pageWidth={meta?.width || 0}
              pageHeight={meta?.height || 0}
              {box}
              {side}
            />
          </div>
          <div class="head-right">
            <code class="boxtxt">
              {#if box}
                x={Math.round(box.x)} y={Math.round(box.y)} w={Math.round(box.w)} h={Math.round(box.h)}
              {:else}
                未选中
              {/if}
            </code>
            <label class="gap">
              连通间隙
              <input type="number" min="0" max="6" step="1" bind:value={gap} />
              <span class="muted">(1 格 ≈ {meta ? Math.round(meta.width / 2200 * 10) / 10 : '1.5'} 页 px)</span>
            </label>
          </div>
        </div>
        <div class="row head-buttons">
          <button onclick={refitSelected} disabled={sel < 0}>贴合墨迹</button>
          <button
            onclick={snapConnectedAll}
            disabled={!boxes.length}
            title="按墨迹连通块把本页每个框贴成「一个连通字」（单按 Shift）"
          >
            连通吸附
          </button>
          <button class="danger" onclick={deleteBox} disabled={sel < 0}>删除框</button>
          <button onclick={addBox}>新建框</button>
        </div>
      </div>

      <GlyphPanel
        {boxes}
        pageWidth={meta?.width || 0}
        pageHeight={meta?.height || 0}
        {sel}
        {review}
        {showRev}
        {bookId}
        {pageId}
        columns={BOOK_COLUMNS[bookId] ?? 1}
        onselect={(i) => (sel = i)}
        onedit={(i) => {
          sel = i
          stage?.focusBox(i)
        }}
      />

      <details class="side-more" bind:open={moreOpen}>
        <summary>参数 / 工具 / 说明（F2）</summary>
      <div class="side-body">

      {#if book?.params?.length}
        <h3 style="margin-top:14px">检测参数</h3>        {#each book.params as p (p.key)}
          <div class="prow">
            <span title={p.help || ''}>{p.label}</span>
            {#if p.type === 'enum'}
              <select value={params[p.key] ?? p.default} onchange={(e) => (params = { ...params, [p.key]: e.currentTarget.value })}>
                {#each p.options as o}<option value={o.value}>{o.label}</option>{/each}
              </select>
            {:else if p.type === 'bool'}
              <input
                type="checkbox"
                checked={!!(params[p.key] ?? p.default)}
                onchange={(e) => (params = { ...params, [p.key]: e.currentTarget.checked })}
              />
            {:else}
              <input
                type="number"
                step={p.step ?? 1}
                value={params[p.key] ?? p.default}
                oninput={(e) => (params = { ...params, [p.key]: Number(e.currentTarget.value) })}
              />
            {/if}
          </div>
        {/each}
      {/if}

      <h3 style="margin-top:14px">本页工具</h3>
      <div class="row">
        <button onclick={runCheck} disabled={!pageId}>合法性检查</button>
        <button onclick={openHistory} disabled={!pageId}>编辑历史</button>
      </div>
      {#if bookId === 'zihai'}
        <button onclick={() => (alignOpen = true)} title="字海1.0 / 卷1 / 卷2 三方逐行对照">
          对齐表（本页）
        </button>
      {/if}
      <button onclick={() => (sheetOpen = true)} title="一列图 + 一列文本，手工配行后导出 TSV">
        手工对齐
      </button>
      <div class="hint">
        全部字（右侧栏下半部分，常驻）：每个字的切图 + 它在原图上的切框，点一格跳回画布并选中该框，
        翻页不打断；连通吸附：框只贴到框内连通块（一个字的笔画并集）的外接框上，拖动时也按这个吸附；
        合法性检查：出界/重复/尺寸离群/空白框，
        汉字海另做编号（条目号缺号、重复、倒序）与部首/笔画/码位检查；编辑历史：本工具每次存盘的快照
        可恢复，汉字海还能看上游 `edits.ndjson` 的人工改动。
      </div>

      <h3>操作</h3>
      <div class="hint">
        框有 <b>8 个手柄</b>（四角 + 四边中点）：拖角＝两边一起动，拖边＝只动一边<br />
        拖动时<b>连通吸附</b>：边拖到离墨迹（同一字的笔画并集）10px 以内才吸附上去，
        其余时候跟着光标走；按住 <span class="kbd">Alt</span> 完全不吸附<br />
        <b>滚轮</b>＝上下滚动 · <b>Alt+滚轮</b>＝以光标为中心缩放 · <b>中键/Space</b> 拖动＝平移<br />
        <span class="kbd">Ctrl</span>+<span class="kbd">Z</span> 撤销 ·
        <span class="kbd">Ctrl</span>+<span class="kbd">Shift</span>+<span class="kbd">Z</span> 重做<br />
        <span class="kbd">Enter</span> 标记完成并翻到下一页 · <span class="kbd">0</span> 适合窗口 ·
        <span class="kbd">1</span> 100% · <span class="kbd">D</span> 黑夜模式<br />
        <span class="kbd">←</span><span class="kbd">→</span><span class="kbd">↑</span><span class="kbd">↓</span>
        微调 1px（<span class="kbd">Shift</span> 为 10px）<br />
        <span class="kbd">Delete</span> 删除 · <span class="kbd">Tab</span> 下一个框 ·
        <span class="kbd">F</span> 贴合墨迹 · 单按 <span class="kbd">Shift</span> 本页连通吸附 ·
        <span class="kbd">A</span> 本页全部重贴<br />
        空白处按住拖动 = 拉一个新框（松手自动吸附）
      </div>

      <h3>说明</h3>
      <div class="hint">
        绿框＝自动检测，橙框＝改过的。开启「标红待查」后，红框是机器猜的可疑框
        （<b>判据不完美，会误报</b>，只当提示）。<br />
        <b>改动自动写盘</b>：停手约 1 秒就存，切页 / 切后台 / 关窗口前也会先写。
        左栏底部显示「已写盘 / 待写盘 / 写盘中…」。<br />
        同一页的写是串行的，每次带版本号；如果别处也改了同一页会显示<b>写盘冲突</b>，
        由你选「覆盖保存」还是「重新载入」——不会悄悄丢掉一边的改动。<br />
        <span class="kbd">Enter</span> 只是「标记完成并翻页」，不需要靠它保存。
      </div>
      {#if message}<div class="hint ok">{message}</div>{/if}
      {#if error}<div class="hint err">{error}</div>{/if}
      </div>
      </details>
    </aside>
  </main>
</div>

{#if settingsOpen}
  <SettingsDialog {settings} {books} onclose={() => (settingsOpen = false)} onsave={saveSettings} />
{/if}

{#if checkOpen}
  <CheckModal
    report={checkReport}
    loading={checkLoading}
    onselect={(i) => {
      sel = i
      stage?.focusBox(i)
      checkOpen = false
    }}
    onrecheck={runCheck}
    onclose={() => (checkOpen = false)}
  />
{/if}

{#if historyOpen}
  <HistoryModal
    items={historyItems}
    upstream={historyUpstream}
    loading={historyLoading}
    onrestore={restoreSnapshot}
    onclose={() => (historyOpen = false)}
  />
{/if}

{#if sheetOpen}
  <SheetView {books} {bookId} {pageId} onclose={() => (sheetOpen = false)} />
{/if}

{#if alignOpen}
  <AlignTable
    bookId={bookId}
    page={printedPage}
    ongoto={openAt}
    onclose={() => (alignOpen = false)}
  />
{/if}
