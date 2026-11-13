<script>
  import { api } from '../lib/api.js'

  let {
    books = [],
    bookId = '',
    pageId = '',
    onclose = () => {},
  } = $props()

  let sheet = $state(null)
  let selected = $state(0)
  let busy = $state(false)
  let error = $state('')
  let message = $state('')

  // 新建表单
  let creating = $state(false)
  let newName = $state('')
  let newFolder = $state('')
  let newTexts = $state('')
  let newTsv = $state('')
  let usePage = $state(true)

  const rows = $derived(sheet?.rows || [])
  const images = $derived(sheet?.images || [])

  async function open(id) {
    busy = true
    error = ''
    try {
      sheet = await api.sheet(id)
      selected = 0
    } catch (err) {
      error = err.message
    } finally {
      busy = false
    }
  }

  async function create() {
    busy = true
    error = ''
    try {
      const created = await api.createSheet({
        name: newName,
        book: usePage ? bookId : '',
        page: usePage ? pageId : '',
        folder: usePage ? '' : newFolder,
        texts: newTsv ? '' : newTexts,
        tsv_path: newTsv,
      })
      sheet = created
      selected = 0
      creating = false
      message = `已建表：${created.images?.length || 0} 张图 / ${created.rows?.length || 0} 行`
    } catch (err) {
      error = err.message
    } finally {
      busy = false
    }
  }

  /** 每次改动都整表存（行很少），rev 对不上就提示，不覆盖别人的表。 */
  async function persist() {
    if (!sheet) return
    try {
      const res = await api.saveSheet(sheet.id, {
        rows: sheet.rows,
        name: sheet.name,
        base_rev: sheet.rev,
      })
      sheet.rev = res.rev
    } catch (err) {
      error = err.message
    }
  }

  function changed() {
    message = ''
    void persist()
  }

  function imgUrl(row) {
    if (!sheet || row.img == null || row.img >= images.length) return ''
    return api.sheetImageUrl(sheet.id, row.img)
  }

  // ── 编辑操作（对应旧 table_view 的 Ctrl+I / Ctrl+U / Shift+↑↓）──────
  function mergeDown(i) {
    if (i < 0 || i >= rows.length - 1) return
    const cur = rows[i]
    const next = rows[i + 1]
    cur.text = [cur.text, next.text].filter(Boolean).join('')
    cur.note = [cur.note, next.note].filter(Boolean).join(' / ')
    rows.splice(i + 1, 1)
    changed()
  }

  function splitAt(i, caret) {
    const cur = rows[i]
    if (!cur) return
    const text = cur.text || ''
    const at = caret ?? text.length
    const rest = text.slice(at)
    cur.text = text.slice(0, at)
    rows.splice(i + 1, 0, { img: null, text: rest, note: '' })
    selected = i + 1
    changed()
  }

  function swapRow(i, delta) {
    const j = i + delta
    if (i < 0 || j < 0 || j >= rows.length) return
    const tmp = rows[i]
    rows[i] = rows[j]
    rows[j] = tmp
    selected = j
    changed()
  }

  function swapImg(i, delta) {
    const j = i + delta
    if (i < 0 || j < 0 || j >= rows.length) return
    const a = rows[i].img
    rows[i].img = rows[j].img
    rows[j].img = a
    changed()
  }

  function bindImage(i) {
    // 把第一个还没被占用的图 slot 绑到这一行
    const used = new Set(rows.map((r) => r.img).filter((v) => v != null))
    for (let k = 0; k < images.length; k++) {
      if (!used.has(k)) {
        rows[i].img = k
        changed()
        return
      }
    }
    error = '所有图都已经配上行了'
  }

  function unbind(i) {
    rows[i].img = null
    changed()
  }

  function addRow(i) {
    rows.splice(i + 1, 0, { img: null, text: '', note: '' })
    selected = i + 1
    changed()
  }

  function removeRow(i) {
    rows.splice(i, 1)
    if (selected >= rows.length) selected = rows.length - 1
    changed()
  }

  function onKey(ev, i) {
    if (ev.key === 'ArrowDown' && ev.shiftKey) {
      ev.preventDefault()
      swapRow(i, 1)
    } else if (ev.key === 'ArrowUp' && ev.shiftKey) {
      ev.preventDefault()
      swapRow(i, -1)
    } else if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault()
      selected = Math.min(i + 1, rows.length - 1)
      document.querySelector(`#cell-${selected}`)?.focus()
    } else if (ev.key === 'i' && (ev.ctrlKey || ev.metaKey)) {
      ev.preventDefault()
      mergeDown(i)
    } else if (ev.key === 'u' && (ev.ctrlKey || ev.metaKey)) {
      ev.preventDefault()
      splitAt(i, ev.target.selectionStart ?? 0)
    }
  }

  async function exportTsv() {
    if (!sheet) return
    const def = `exports/${sheet.id}.tsv`
    const path = prompt('导出 TSV 到（相对工作区或绝对路径）', def)
    if (!path) return
    try {
      const res = await api.exportSheet(sheet.id, path)
      message = `已导出 ${res.rows} 行 → ${res.path}`
    } catch (err) {
      error = err.message
    }
  }

  // 打开面板时列出已有的表，优先打开最近一张
  $effect(() => {
    void (async () => {
      try {
        const res = await api.sheets()
        if (res.sheets?.length && !sheet) await open(res.sheets[0].id)
      } catch {
        /* 列表拿不到就等用户新建 */
      }
    })()
  })
</script>

<div class="dialog-backdrop" role="presentation" onclick={(e) => e.target === e.currentTarget && onclose()}>
  <div class="dialog wide sheet">
    <div class="dialog-head">
      <h3>{sheet ? `手工对齐 · ${sheet.name}` : '手工对齐'}</h3>
      <div class="row" style="margin:0">
        <button onclick={() => (creating = !creating)}>{creating ? '取消新建' : '新建'}</button>
        <button onclick={exportTsv} disabled={!sheet}>导出 TSV</button>
        <button onclick={onclose}>关闭</button>
      </div>
    </div>

    {#if creating}
      <div class="sheet-new">
        <div class="row">
          <label><input type="radio" bind:group={usePage} value={true} /> 用当前页的切图</label>
          <label><input type="radio" bind:group={usePage} value={false} /> 用本地文件夹</label>
          {#if usePage}
            <code class="mono">{bookId} · {pageId || '未选页'}</code>
          {:else}
            <input type="text" bind:value={newFolder} placeholder="图片文件夹路径（按文件名自然序）" style="width:340px" />
          {/if}
        </div>
        <div class="row">
          <input type="text" bind:value={newName} placeholder="表名（可空）" style="width:220px" />
        </div>
        <div class="row">
          <input
            type="text"
            bind:value={newTsv}
            placeholder="或读一份 TSV（号/图/文本/备注；相对路径按工作目录）"
            style="width:420px"
          />
        </div>
        {#if !newTsv}
          <textarea bind:value={newTexts} rows="4" placeholder="把一列文本粘在这里（每行一个字/一条；也认 号\t图\t文本\t备注）"></textarea>
        {/if}
        <div class="row">
          <button class="primary" onclick={create} disabled={busy}>建表</button>
        </div>
      </div>
    {/if}

    {#if error}<div class="hint err">{error}</div>{/if}
    {#if message}<div class="hint ok">{message}</div>{/if}

    {#if sheet}
      <div class="sheet-bar">
        <span class="muted">
          {images.length} 张图 · {rows.length} 行 ·
          未配图 {rows.filter((r) => r.img == null).length} 行
        </span>
        <span style="flex:1"></span>
        <span class="muted">
          <b>Ctrl+I</b> 合并下一行 · <b>Ctrl+U</b> 光标处拆分 · <b>Shift+↑↓</b> 换行 · <b>Enter</b> 下一行
        </span>
      </div>
      <div class="sheet-table">
        <div class="sheet-row head">
          <span>#</span>
          <span>图</span>
          <span>文本</span>
          <span>备注</span>
          <span></span>
        </div>
        {#each rows as row, i (i)}
          <div class="sheet-row" class:sel={i === selected} class:empty={row.img == null}>
            <span class="no">{i + 1}</span>
            <span class="img">
              {#if imgUrl(row)}
                <img src={imgUrl(row)} alt={`图 ${row.img}`} />
              {:else}
                <button class="bind" onclick={() => bindImage(i)} title="绑定下一个还没用的图">配图</button>
              {/if}
            </span>
            <input
              id={`cell-${i}`}
              type="text"
              bind:value={row.text}
              onfocus={() => (selected = i)}
              onblur={changed}
              onkeydown={(ev) => onKey(ev, i)}
            />
            <input type="text" bind:value={row.note} onblur={changed} placeholder="" />
            <span class="ops">
              <button onclick={() => swapImg(i, -1)} title="图和上一行对调">↑图</button>
              <button onclick={() => swapImg(i, 1)} title="图和下一行对调">↓图</button>
              <button onclick={() => unbind(i)} title="解绑这张图">解绑</button>
              <button onclick={() => addRow(i)} title="在下面插入一行">+行</button>
              <button class="danger" onclick={() => removeRow(i)} title="删掉这一行">✕</button>
            </span>
          </div>
        {/each}
      </div>
    {:else if !creating}
      <div class="hint">还没有表。点「新建」从当前页切图或本地文件夹建一张。</div>
    {/if}
  </div>
</div>
