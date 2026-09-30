/** 统一请求封装：拼后端地址、自动带会话凭证、写操作遇 409 自动重同步重试一次。 */
import { useSessionStore } from '@/stores/session'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export class ApiError extends Error {
  status: number
  code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

export function buildUrl(path: string): string {
  return path.startsWith('http') ? path : `${API_BASE}${path}`
}

function sessionHeaders(extra?: HeadersInit): HeadersInit {
  const store = useSessionStore()
  return {
    'Content-Type': 'application/json',
    ...(store.sessionId ? { 'X-Session-Id': store.sessionId } : {}),
    ...extra,
  }
}

export function request(path: string, init?: RequestInit): Promise<Response> {
  return fetch(buildUrl(path), { ...init, headers: sessionHeaders(init?.headers) }).catch(
    (error: unknown) => {
      const detail = error instanceof Error ? error.message : '请求未送达'
      throw new Error(`接口请求失败：${detail}`)
    },
  )
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}，数据未更新`)
  }
  return (await response.json()) as T
}

/** 登录建立会话 */
export async function login(accountId: string): Promise<void> {
  const store = useSessionStore()
  const response = await fetch(buildUrl('/api/security/sessions'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ account_id: accountId }),
  })
  const payload = await response.json()
  if (!response.ok) {
    throw new ApiError(response.status, payload?.detail?.code ?? 'login_failed',
      payload?.detail?.message ?? '登录失败')
  }
  store.setSession(payload)
}

/** 调班/撤权后旧会话写操作拿到 409：刷新 auth_epoch 后由调用方重试 */
async function resyncSession(): Promise<boolean> {
  const store = useSessionStore()
  if (!store.sessionId) return false
  const response = await fetch(buildUrl('/api/security/sessions/resync'), {
    method: 'POST',
    headers: sessionHeaders(),
  })
  if (response.ok) {
    store.setSession(await response.json())
    return true
  }
  store.logout()
  return false
}

/**
 * 带权限语义的写请求：
 * - 409 session_stale：自动 resync 并重试一次（旧会话不得继续提交的硬性约束在服务端保证）
 * - 其他 4xx：抛出 ApiError，保留 code 供页面提示“只读/越权”等原因
 */
export async function writeJson<T>(
  path: string,
  body: unknown,
  method: 'POST' | 'PUT' | 'DELETE' = 'POST',
): Promise<T> {
  let response = await request(path, {
    method,
    body: JSON.stringify(body ?? {}),
  })

  if (response.status === 409) {
    const errPayload = await response.json().catch(() => null)
    if (errPayload?.detail?.code === 'session_stale' && await resyncSession()) {
      response = await request(path, { method, body: JSON.stringify(body ?? {}) })
    } else {
      throw new ApiError(409, errPayload?.detail?.code ?? 'conflict',
        errPayload?.detail?.message ?? '操作冲突，请刷新后重试')
    }
  }

  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = payload?.detail
    const message = typeof detail === 'object' ? detail?.message : detail
    const code = typeof detail === 'object' ? detail?.code : `http_${response.status}`
    throw new ApiError(response.status, code ?? `http_${response.status}`,
      message ?? '操作未生效')
  }
  return payload as T
}
