const BASE = ''

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

async function request(path, options = {}) {
  const response = await fetch(BASE + path, {
    headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
    ...options,
  })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    let body = null
    try {
      const data = await response.json()
      body = data.detail ?? null
      if (data.detail) detail = typeof data.detail === 'string' ? data.detail : (data.detail.message ?? JSON.stringify(data.detail))
    } catch {
      /* 不是 JSON 就用状态码 */
    }
    throw new ApiError(detail, response.status, body)
  }
  if (response.status === 204) return null
  return response.json()
}

const enc = encodeURIComponent

export const api = {
  health: () => request('/api/health'),
  books: () => request('/api/books'),
  book: (id) => request(`/api/books/${enc(id)}`),
  pages: (id) => request(`/api/books/${enc(id)}/pages`),
  meta: (book, page) => request(`/api/books/${enc(book)}/pages/${enc(page)}/meta`),
  previewUrl: (book, page, maxWidth) =>
    `/api/books/${enc(book)}/pages/${enc(page)}/preview${maxWidth ? `?max_width=${maxWidth}` : ''}`,
  cropUrl: (book, page, box, maxWidth = 240) =>
    `/api/books/${enc(book)}/pages/${enc(page)}/crop?x=${box.x}&y=${box.y}&w=${box.w}&h=${box.h}&max_width=${maxWidth}`,
  boxes: (book, page) => request(`/api/books/${enc(book)}/pages/${enc(page)}/boxes`),
  saveBoxes: (book, page, payload) =>
    request(`/api/books/${enc(book)}/pages/${enc(page)}/boxes`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  /** 页面卸载时用：keepalive 让请求活过当前文档。 */
  saveBoxesKeepalive: (book, page, payload) =>
    fetch(`${BASE}/api/books/${enc(book)}/pages/${enc(page)}/boxes`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(() => null),
  detect: (book, page, params, save = true) =>
    request(`/api/books/${enc(book)}/pages/${enc(page)}/detect`, {
      method: 'POST',
      body: JSON.stringify({ params, save }),
    }),
  refine: (book, page, boxes, params = {}, save = false) =>
    request(`/api/books/${enc(book)}/pages/${enc(page)}/refine`, {
      method: 'POST',
      body: JSON.stringify({ boxes, params, save }),
    }),
  setState: (book, page, state) =>
    request(`/api/books/${enc(book)}/pages/${enc(page)}/state`, {
      method: 'POST',
      body: JSON.stringify(state),
    }),
  nav: (book, after, mode) =>
    request(`/api/books/${enc(book)}/nav?after=${enc(after)}&mode=${enc(mode)}`),
  check: (book, page) => request(`/api/books/${enc(book)}/pages/${enc(page)}/check`),
  align: (book, page) => request(`/api/books/${enc(book)}/align?page=${enc(page)}`),
  alignSummary: (book) => request(`/api/books/${enc(book)}/align/summary`),
  alignSpriteUrl: (book, page, pos, maxSize = 96) =>
    `/api/books/${enc(book)}/align/sprite?page=${page}&pos=${pos}&max_size=${maxSize}`,
  sheets: () => request('/api/sheets'),
  sheet: (id) => request(`/api/sheets/${enc(id)}`),
  createSheet: (payload) =>
    request('/api/sheets', { method: 'POST', body: JSON.stringify(payload) }),
  saveSheet: (id, payload) =>
    request(`/api/sheets/${enc(id)}`, { method: 'PUT', body: JSON.stringify(payload) }),
  sheetImageUrl: (id, index, maxWidth = 160) =>
    `/api/sheets/${enc(id)}/image/${index}${maxWidth ? `?max_width=${maxWidth}` : ''}`,
  exportSheet: (id, path) =>
    request(`/api/sheets/${enc(id)}/export`, { method: 'POST', body: JSON.stringify({ path }) }),
  history: (book, page) => request(`/api/books/${enc(book)}/pages/${enc(page)}/history`),
  historyItem: (book, page, id) =>
    request(`/api/books/${enc(book)}/pages/${enc(page)}/history/${enc(id)}`),
  restoreHistory: (book, page, id) =>
    request(`/api/books/${enc(book)}/pages/${enc(page)}/history/${enc(id)}/restore`, {
      method: 'POST',
    }),
  exportPage: (book, page, params, outDir) =>
    request(`/api/books/${enc(book)}/pages/${enc(page)}/export`, {
      method: 'POST',
      body: JSON.stringify({ params, out_dir: outDir || null }),
    }),
  detectJob: (book, pages, params) =>
    request('/api/jobs/detect', {
      method: 'POST',
      body: JSON.stringify({ book, pages, params }),
    }),
  exportJob: (book, pages, params, outDir) =>
    request('/api/jobs/export', {
      method: 'POST',
      body: JSON.stringify({ book, pages, params, out_dir: outDir || null }),
    }),
  job: (id, withResult = false) => request(`/api/jobs/${enc(id)}${withResult ? '?result=1' : ''}`),
  cancelJob: (id) => request(`/api/jobs/${enc(id)}/cancel`, { method: 'POST' }),
  progress: (book) => request(`/api/jobs/progress/${enc(book)}`),
  settings: () => request('/api/settings'),
  saveSettings: (patch) =>
    request('/api/settings', { method: 'PUT', body: JSON.stringify(patch) }),
}
