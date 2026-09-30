import { defineStore } from 'pinia'

export type SessionInfo = {
  session_id: string
  account_id: string
  account_name: string
  unit_id: string
  unit_name: string
  auth_epoch: number
}

const STORAGE_KEY = 'geochem.session'

export const useSessionStore = defineStore('session', {
  state: () => ({
    session: null as SessionInfo | null,
    shiftLabel: '白班 08:00-20:00',
    /** 401/403/409 等场景给页面用的提示 */
    lastError: '',
  }),
  getters: {
    operator: (state) => state.session?.account_name ?? '未登录',
    scope: (state) => (state.session ? `${state.session.unit_name} · ${state.session.account_id}` : '未建立会话'),
    canOperate: (state) => state.session !== null,
    sessionId: (state) => state.session?.session_id ?? '',
    unitName: (state) => state.session?.unit_name ?? '',
  },
  actions: {
    setSession(session: SessionInfo) {
      this.session = session
      localStorage.setItem(STORAGE_KEY, JSON.stringify(session))
      this.lastError = ''
    },
    restore() {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (raw) {
        try {
          this.session = JSON.parse(raw) as SessionInfo
        } catch {
          localStorage.removeItem(STORAGE_KEY)
        }
      }
    },
    logout() {
      this.session = null
      localStorage.removeItem(STORAGE_KEY)
    },
    setShift(label: string) {
      this.shiftLabel = label
    },
  },
})
