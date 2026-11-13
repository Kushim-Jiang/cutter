<script>
  let {
    report = null,
    loading = false,
    onselect = () => {},
    onrecheck = () => {},
    onclose = () => {},
  } = $props()
</script>

<div class="dialog-backdrop" onclick={onclose} role="presentation">
  <div
    class="dialog"
    onclick={(e) => e.stopPropagation()}
    onkeydown={(e) => e.stopPropagation()}
    role="dialog"
    tabindex="-1"
    aria-label="合法性检查"
  >
    <div class="dialog-head">
      <h3>合法性检查</h3>
      <div class="row" style="margin: 0;">
        <button onclick={onrecheck} disabled={loading}>{loading ? '检查中…' : '重新检查'}</button>
        <button onclick={onclose}>关闭</button>
      </div>
    </div>

    {#if !report}
      <div class="hint">还没有结果。</div>
    {:else}
      <div class="row">
        <span class="pill err">{report.stats.errors} 错误</span>
        <span class="pill warn">{report.stats.warnings} 警告</span>
        <span class="muted">共 {report.stats.boxes} 个框{report.saved ? '' : '（未存盘，按自动检测结果检查）'}</span>
      </div>

      {#if report.issues.length === 0}
        <div class="hint ok">没有发现问题。</div>
      {/if}

      <div class="issue-list">
        {#each report.issues as it (it)}
          <button
            class="issue"
            class:err={it.level === 'error'}
            class:warn={it.level === 'warn'}
            onclick={() => it.boxes.length && onselect(it.boxes[0])}
            disabled={!it.boxes.length}
          >
            <span class="tag">{it.level === 'error' ? '错误' : '提示'}</span>
            <span class="kind">{it.kind}</span>
            <span class="msg">{it.message}</span>
            {#if it.boxes.length}
              <span class="no">→ 框 {it.boxes.map((n) => n + 1).join('、')}</span>
            {/if}
          </button>
        {/each}
      </div>
    {/if}
  </div>
</div>
