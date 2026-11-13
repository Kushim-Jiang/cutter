<script>
  let { settings = {}, books = [], onclose = () => {}, onsave = () => {} } = $props()

  // svelte-ignore state_referenced_locally
  let draft = $state(JSON.parse(JSON.stringify(settings)))

  function setBook(id, key, value) {
    draft.books[id] = { ...(draft.books[id] || {}), [key]: value }
  }

  function setVol(id, vol, key, value) {
    const book = draft.books[id] || {}
    const volumes = { ...(book.volumes || {}) }
    volumes[vol] = { ...(volumes[vol] || {}), [key]: value }
    draft.books[id] = { ...book, volumes }
  }

  function commit() {
    onsave(draft)
  }
</script>

<div class="dialog-backdrop" onclick={onclose} role="presentation">
  <div
    class="dialog"
    onclick={(e) => e.stopPropagation()}
    onkeydown={(e) => e.stopPropagation()}
    role="dialog"
    tabindex="-1"
    aria-label="设置"
  >
    <h3>设置</h3>
    <div class="field">
      <label for="export-dir">导出目录</label>
      <input id="export-dir" bind:value={draft.export_dir} />
    </div>
    <div class="field">
      <label for="preview-width">预览宽度</label>
      <input id="preview-width" type="number" bind:value={draft.preview_width} />
    </div>
    <div class="field">
      <span class="lbl">上游定稿</span>
      <label class="row" style="margin: 0;">
        <input type="checkbox" bind:checked={draft.use_seed} style="width: 16px;" />
        <span>打开页面时直接用已有切好的数据（中华字海 edits.json / 汉字海 ZI·ZJ / 康熙 字头清单）</span>
      </label>
    </div>

    <div class="section-title" style="margin-top: 16px;">书源</div>
    {#each books as book (book.id)}
      <div style="border: 1px solid var(--line); border-radius: 8px; padding: 10px; margin-bottom: 10px;">
        <div class="row" style="margin-bottom: 6px;">
          <strong>{book.name}</strong>
          <span style="flex: 1;"></span>
          <span class="badge" class:ok={book.status.available} class:bad={!book.status.available}>
            {book.status.available ? '可用' : '未配置'}
          </span>
        </div>
        {#if book.id === 'kx'}
          <div class="field">
            <span class="lbl">PDF 目录</span>
            <input value={draft.books?.kx?.book_dir ?? ''} oninput={(e) => setBook('kx', 'book_dir', e.currentTarget.value)} />
          </div>
        {:else if book.id === 'zihai'}
          <div class="field">
            <span class="lbl">卷 1 PDF</span>
            <input
              value={draft.books?.zihai?.volumes?.['1']?.pdf ?? draft.books?.zihai?.pdf ?? ''}
              oninput={(e) => setVol('zihai', '1', 'pdf', e.currentTarget.value)}
              placeholder="中华字海1a.pdf"
            />
          </div>
          <div class="field">
            <span class="lbl">卷 2 PDF（可选）</span>
            <input
              value={draft.books?.zihai?.volumes?.['2']?.pdf ?? ''}
              oninput={(e) => setVol('zihai', '2', 'pdf', e.currentTarget.value)}
              placeholder="中华字海2.pdf"
            />
          </div>
          <div class="field">
            <span class="lbl">卷 1 / 卷 2 印刷页偏移</span>
            <div class="row">
              <input
                type="number"
                value={draft.books?.zihai?.volumes?.['1']?.offset ?? draft.books?.zihai?.offset ?? 83}
                oninput={(e) => setVol('zihai', '1', 'offset', Number(e.currentTarget.value))}
              />
              <input
                type="number"
                value={draft.books?.zihai?.volumes?.['2']?.offset ?? 79}
                oninput={(e) => setVol('zihai', '2', 'offset', Number(e.currentTarget.value))}
              />
            </div>
          </div>
          <div class="field">
            <span class="lbl">PDF 页码范围</span>
            <div class="row">
              <input type="number" value={draft.books?.zihai?.first_page ?? 84} oninput={(e) => setBook('zihai', 'first_page', Number(e.currentTarget.value))} />
              <span>—</span>
              <input type="number" value={draft.books?.zihai?.last_page ?? 1864} oninput={(e) => setBook('zihai', 'last_page', Number(e.currentTarget.value))} />
            </div>
          </div>
        {:else}
          <div class="field">
            <span class="lbl">扫描 PDF</span>
            <input value={draft.books?.[book.id]?.pdf ?? ''} oninput={(e) => setBook(book.id, 'pdf', e.currentTarget.value)} />
          </div>
          <div class="field">
            <span class="lbl">PDF 页码范围</span>
            <div class="row">
              <input type="number" value={draft.books?.[book.id]?.first_page ?? 1} oninput={(e) => setBook(book.id, 'first_page', Number(e.currentTarget.value))} />
              <span>—</span>
              <input type="number" value={draft.books?.[book.id]?.last_page ?? 0} oninput={(e) => setBook(book.id, 'last_page', Number(e.currentTarget.value))} />
            </div>
          </div>
          {#if book.id === 'zihai' || book.id === 'hzh'}
            <div class="field">
              <span class="lbl">印刷页偏移</span>
              <input type="number" value={draft.books?.[book.id]?.offset ?? 0} oninput={(e) => setBook(book.id, 'offset', Number(e.currentTarget.value))} />
            </div>
          {/if}
        {/if}
        {#if !book.status.available && book.status.reason}
          <div class="muted" style="font-size: 12px;">{book.status.reason}</div>
        {/if}
      </div>
    {/each}

    <div class="row" style="justify-content: flex-end; margin-top: 12px;">
      <button onclick={onclose}>取消</button>
      <button class="primary" onclick={commit}>保存并重载</button>
    </div>
  </div>
</div>
