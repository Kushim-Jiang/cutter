<script>
  let { items = [], upstream = [], loading = false, onrestore = () => {}, onclose = () => {} } = $props()
  let tab = $state('local')
  let restoring = $state('')
</script>

<div class="dialog-backdrop" onclick={onclose} role="presentation">
  <div
    class="dialog"
    onclick={(e) => e.stopPropagation()}
    onkeydown={(e) => e.stopPropagation()}
    role="dialog"
    tabindex="-1"
    aria-label="编辑历史"
  >
    <div class="dialog-head">
      <h3>编辑历史</h3>
      <button onclick={onclose}>关闭</button>
    </div>

    <div class="tabs" style="margin-bottom: 10px;">
      <button class:active={tab === 'local'} onclick={() => (tab = 'local')}>
        本工具保存记录（{items.length}）
      </button>
      <button class:active={tab === 'upstream'} onclick={() => (tab = 'upstream')}>
        汉字海上游记录（{upstream.length}）
      </button>
    </div>

    {#if tab === 'local'}
      {#if loading}
        <div class="hint">读取中…</div>
      {:else if !items.length}
        <div class="hint">这一页还没有保存记录。每次「回车存盘 / 本页完成 / 本页还原」都会留一份快照。</div>
      {:else}
        <div class="issue-list">
          {#each items as it (it.id)}
            <div class="history-row">
              <span class="time">{it.time}</span>
              <span class="muted">{it.count} 个框</span>
              {#if it.state?.done}<span class="pill ok">已完成</span>{/if}
              {#if it.state?.suspect}<span class="pill warn">待查</span>{/if}
              <span style="flex: 1;"></span>
              <button
                onclick={async () => {
                  restoring = it.id
                  await onrestore(it.id)
                  restoring = ''
                }}
                disabled={!!restoring}
              >
                {restoring === it.id ? '恢复中…' : '恢复到这一版'}
              </button>
            </div>
          {/each}
        </div>
        <div class="hint">恢复只改内存，按 Enter 才写盘（与校对台一致）。</div>
      {/if}
    {:else}
      {#if !upstream.length}
        <div class="hint">
          这一页没有上游记录（`_zihai/hh/edits.ndjson`）。只对《汉字海》有数据。
        </div>
      {:else}
        <div class="issue-list">
          {#each upstream as it (it.id)}
            <div class="history-row upstream">
              <span class="time">{it.time}</span>
              <span class="kind">{it.action}</span>
              {#if it.entry}<span class="muted">条目 {it.entry}</span>{/if}
              {#if it.before?.x !== undefined}
                <span class="muted mono">
                  {Math.round(it.before.x)},{Math.round(it.before.y)}
                  {Math.round(it.before.w)}×{Math.round(it.before.h)}
                </span>
              {/if}
              {#if it.after?.x !== undefined}
                <span class="mono">→ {Math.round(it.after.x)},{Math.round(it.after.y)}
                  {Math.round(it.after.w)}×{Math.round(it.after.h)}</span>
              {/if}
              {#if it.memo}<span class="muted">「{it.memo}」</span>{/if}
            </div>
          {/each}
        </div>
        <div class="hint">上游记录只读，用来对照当时为什么改这一框。</div>
      {/if}
    {/if}
  </div>
</div>
