// 图片加载失败自动重试。
// 切图是一次一页几十张并发拉的，网络抖一下或服务端刚重启就会漏几张，
// 漏了不该一直空着——退避重试几次，加 _rt 参数避开缓存里的失败结果。
const MAX_TRIES = 4
const BASE_DELAY = 250

/**
 * Svelte action：`<img src={url} use:autoRetry={url} />`
 * 传入的 src 变化时重置计数（换页 / 换书后重新有机会重试）。
 */
export function autoRetry(node, src) {
  let base = src
  let tries = 0
  let timer = null
  let failed = false

  const stop = () => {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  const onError = () => {
    if (tries >= MAX_TRIES) {
      failed = true
      node.dispatchEvent(new CustomEvent('giveup', { bubbles: false }))
      return
    }
    tries += 1
    stop()
    timer = setTimeout(() => {
      timer = null
      if (failed) return
      try {
        const url = new URL(base, location.href)
        url.searchParams.set('_rt', String(tries))
        node.src = url.toString()
      } catch {
        node.src = base
      }
    }, BASE_DELAY * tries)
  }

  node.addEventListener('error', onError)

  return {
    update(next) {
      if (next === base) return
      base = next
      tries = 0
      failed = false
      stop()
    },
    destroy() {
      node.removeEventListener('error', onError)
      stop()
    },
  }
}
