<script>
  import { api } from '../lib/api.js'

  let {
    bookId = 'zihai',
    page = 1,
    ongoto = () => {},
    onclose = () => {},
  } = $props()

  let printed = $state(1)
  let data = $state(null)
  let loading = $state(false)
  let error = $state('')
  let onlyBad = $state(false)

  const CROP = 48

  async function load(target) {
    const next = Math.max(1, Math.min(2000, Number(target) || 1))
    loading = true
    error = ''
    try {
      data = await api.align(bookId, next)
      printed = next
    } catch (err) {
      error = err.message
      data = null
    } finally {
      loading = false
    }
  }

  $effect(() => {
    void load(page)
  })

  const rows = $derived(data?.rows || [])

  function crop(box) {
    return api.cropUrl(bookId, box.page, box, CROP * 2)
  }

  function jump(box) {
    if (box) ongoto(box.page, box.idx)
  }

  const issues = $derived(
    rows.filter((r) => !r.v1 || !r.v2 || !r.char || r.note)
  )
</script>

<div class="dialog-backdrop" role="presentation" onclick={(e) => e.target === e.currentTarget && onclose()}>
  <div class="dialog wide align">
    <div class="dialog-head">
      <h3>对齐表 · 第 {printed} 页</h3>
      <div class="row" style="margin:0">
        <button onclick={() => load(printed - 1)} disabled={printed <= 1 || loading}>◀</button>
        <input
          type="number"
          min="1"
          max="2000"
          value={printed}
          onchange={(e) => load(e.currentTarget.value)}
          style="width:80px"
        />
        <button onclick={() => load(printed + 1)} disabled={printed >= 1781 || loading}>▶</button>
        <button
          class:on={onlyBad}
          onclick={() => (onlyBad = !onlyBad)}
          title="只显示缺字/缺框/有标注的行"
        >
          只看有问题（{issues.length}）
        </button>
        <button onclick={onclose}>关闭</button>
      </div>
    </div>

    {#if data}
      <div class="align-bar">
        <span class="pill" class:ok={data.aligned} class:err={!data.aligned}>
          {data.aligned ? '位置对齐' : '对不上'}
        </span>
        <span class="muted">
          字海1.0 {data.counts.zh} 条 · 卷1 {data.counts.v1} 框 · 卷2 {data.counts.v2} 框
          （有效 {data.effective.v1} / {data.effective.v2}）
        </span>
        {#if data.notes}<span class="pill warn">本页有核实标注</span>{/if}
        <span style="flex:1"></span>
        <button onclick={() => ongoto(data.v1_page, data.rows[0]?.v1?.idx ?? -1)}>改卷1</button>
        <button onclick={() => ongoto(data.v2_page, data.rows[0]?.v2?.idx ?? -1)}>改卷2</button>
      </div>
    {/if}

    {#if loading}
      <div class="overlay-hint small">计算中…</div>
    {:else if error}
      <div class="hint err">{error}</div>
    {:else if data}
      <div class="align-table">
        <div class="align-row head">
          <span>编号</span>
          <span>字海1.0 字 / ziid</span>
          <span>卷1 切图</span>
          <span>卷2 切图</span>
          <span>备注</span>
        </div>
        {#each rows as row (row.pos)}
          {#if !onlyBad || !row.v1 || !row.v2 || !row.char || row.note}
            <div class="align-row" class:bad={!row.v1 || !row.v2 || !row.char}>
              <span class="pos">{row.pos}</span>
              <span class="zh" title={row.standard_form ? `正字：${row.standard_form}` : ''}>
                {#if row.sprite && !row.char}
                  <img class="sprite" src={api.alignSpriteUrl(bookId, printed, row.pos)} alt={`书影 ${row.pos}`} />
                {:else}
                  <b>{row.char || '—'}</b>
                {/if}
                <code class="mono">{row.ziid || ''}</code>
                {#if row.standard_form && row.standard_form !== row.char}
                  <span class="muted">→{row.standard_form}</span>
                {/if}
              </span>
              {#each [['v1', row.v1], ['v2', row.v2]] as [side, box] (side)}
                <span class="cut">
                  {#if box}
                    <button class="thumb" onclick={() => jump(box)} title="跳到编辑器里的这个框">
                      <img src={crop(box)} alt={`${side} ${row.pos}`} />
                    </button>
                  {:else}
                    <span class="dash">—</span>
                  {/if}
                </span>
              {/each}
              <span class="note">{row.note || ''}</span>
            </div>
          {/if}
        {/each}
      </div>
    {/if}
  </div>
</div>
