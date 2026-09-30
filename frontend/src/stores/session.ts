import { defineStore } from 'pinia'

import { request } from '@/api/client'

export type Account = {
  username: string
  name: string
  unit_code: string
  is_admin: boolean
}

type SessionState = {
  token: string
  account: Account | null
  openedAt: string
  /** 401 后由拦截器置位，页面据此提示重新登录/交班 */
  authError: string
}

const TOKEN_KEY = 'geo.session.token'

export const useSessionStore = defineStore('session', {
  state: (): SessionState => ({
    token: localStorage.getItem(TOKEN_KEY) ?? '',
    account: null,
    openedAt: '',
    authError: '',
  }),
  getters: {
    isLoggedIn: (state) => state.token.length > 0,
    unitName: (state) => state.account?.unit_code ?? '',
  },
  actions: {
    async login(username: string) {
      const response = await request('/api/access/sessions', {
        method: 'POST',
        body: JSON.stringify({ username }),
      })
      if (!response.ok) {
        throw new Error((await response.json()).detail ?? '登录失败')
      }
      const payload = await response.json()
      this.token = payload.token as string
      this.account = payload.account as Account
      this.openedAt = payload.opened_at as string
      this.authError = ''
      localStorage.setItem(TOKEN_KEY, this.token)
    },

    async restore() {
      if (!this.token) return
      const response = await request('/api/access/me')
      if (response.ok) {
        this.account = (await response.json()) as Account
        this.authError = ''
      } else {
        this.logout('会话已失效，请重新登录或交班')
      }
    },

    /** 并发调班：按操作时间定序；成功后服务端返回接班人新会话。 */
    async handover(newAccount: string, opTime: string) {
      const response = await request('/api/access/handover', {
        method: 'POST',
        body: JSON.stringify({ new_account: newAccount, op_time: opTime }),
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        // 冲突失败时旧会话已被服务端关闭
        if (response.status === 409) this.logout(payload.detail ?? '调班冲突，会话已关闭')
        throw new Error(payload.detail ?? '调班失败')
      }
      this.token = payload.token as string
      this.account = payload.account as Account
      this.openedAt = payload.opened_at as string
      this.authError = ''
      localStorage.setItem(TOKEN_KEY, this.token)
      return payload
    },

    logout(reason = '已退出登录') {
      this.token = ''
      this.account = null
      this.openedAt = ''
      this.authError = reason
      localStorage.removeItem(TOKEN_KEY)
    },
  },
})
