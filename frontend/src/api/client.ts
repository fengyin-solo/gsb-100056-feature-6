/** 统一请求封装：拼后端地址、自动带会话令牌、401 时通知全局清理会话。 */
const API_BASE = import.meta.env.VITE_API_BASE ?? ''
const TOKEN_KEY = 'geo.session.token'

export const SESSION_CLOSED_EVENT = 'geo:session-closed'

function emitSessionClosed(detail: string) {
  window.dispatchEvent(new CustomEvent(SESSION_CLOSED_EVENT, { detail }))
}

export function request(path: string, init?: RequestInit): Promise<Response> {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`
  const token = localStorage.getItem(TOKEN_KEY) ?? ''
  const headers = new Headers(init?.headers ?? { 'Content-Type': 'application/json' })
  if (token) headers.set('X-Session-Token', token)

  return fetch(url, { ...init, headers })
    .then(async (response) => {
      if (response.status === 401) {
        let reason = '会话已关闭，请重新登录或交班'
        try {
          reason = (await response.clone().json()).detail ?? reason
        } catch {
          /* 忽略非 JSON 错误体 */
        }
        localStorage.removeItem(TOKEN_KEY)
        emitSessionClosed(reason)
      }
      return response
    })
    .catch((error: unknown) => {
      const detail = error instanceof Error ? error.message : '请求未送达'
      throw new Error(`接口请求失败：${detail}`)
    })
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}，数据未更新`)
  }
  return (await response.json()) as T
}
