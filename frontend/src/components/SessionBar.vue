<template>
  <div class="session-bar">
    <template v-if="session.account">
      <span class="session-who">
        当前账号：<strong>{{ session.account.name }}</strong>
        <em>{{ session.account.unit_code }}{{ session.account.is_admin ? ' · 管理员' : '' }}</em>
      </span>
      <button class="btn" type="button" @click="showHandover = !showHandover">并发调班</button>
      <button class="btn ghost" type="button" @click="session.logout()">退出</button>
      <form v-if="showHandover" class="handover-form" @submit.prevent="doHandover">
        <label>
          接班人
          <select v-model="nextAccount">
            <option v-for="acc in sameUnitAccounts" :key="acc.username" :value="acc.username">
              {{ acc.name }}（{{ acc.unit_code }}）
            </option>
          </select>
        </label>
        <label>
          操作时间
          <input v-model="opTime" placeholder="2026-09-30T09:00:00+00:00" />
        </label>
        <button class="btn primary" type="submit">提交调班</button>
        <span v-if="handoverError" class="error-text">{{ handoverError }}</span>
      </form>
    </template>
    <template v-else>
      <span class="session-who muted">未登录：请选择账号建立会话</span>
      <select v-model="loginAccount">
        <option value="" disabled>选择账号…</option>
        <option v-for="acc in accounts" :key="acc.username" :value="acc.username">
          {{ acc.name }}（{{ acc.unit_code }}）
        </option>
      </select>
      <button class="btn primary" type="button" :disabled="!loginAccount" @click="doLogin">
        登录
      </button>
      <span v-if="loginError" class="error-text">{{ loginError }}</span>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { SESSION_CLOSED_EVENT } from '@/api/client'
import { useSessionStore, type Account } from '@/stores/session'

const session = useSessionStore()
const accounts = ref<Account[]>([])
const loginAccount = ref('')
const loginError = ref('')
const nextAccount = ref('')
const opTime = ref('')
const showHandover = ref(false)
const handoverError = ref('')

const sameUnitAccounts = computed(() =>
  accounts.value.filter((acc) => acc.unit_code === session.account?.unit_code),
)

async function loadAccounts() {
  // 未登录也允许拉账号清单：该接口本身要求会话，故先尝试用任意只读不行——
  // 改用登录页固定清单兜底 + 已登录后刷新目录。
  if (!session.token) return
  try {
    const response = await fetch('/api/access/catalog', {
      headers: { 'X-Session-Token': session.token },
    })
    if (response.ok) accounts.value = (await response.json()).accounts
  } catch {
    /* 目录拉取失败不阻塞登录 */
  }
}

async function doLogin() {
  loginError.value = ''
  try {
    await session.login(loginAccount.value)
    await loadAccounts()
    if (!sameUnitAccounts.value.some((acc) => acc.username !== session.account?.username)) {
      // 静默：无其他同单位接班人也不影响
    }
  } catch (error) {
    loginError.value = error instanceof Error ? error.message : '登录失败'
  }
}

async function doHandover() {
  handoverError.value = ''
  if (!nextAccount.value || !opTime.value) {
    handoverError.value = '接班人与操作时间都必填'
    return
  }
  try {
    await session.handover(nextAccount.value, opTime.value)
    showHandover.value = false
  } catch (error) {
    handoverError.value = error instanceof Error ? error.message : '调班失败'
  }
}

onMounted(async () => {
  // 登录前候选清单：与后端种子保持一致的最小兜底集
  accounts.value = [
    { username: 'admin.a', name: '大队长（本单位管理员）', unit_code: 'UNIT-A', is_admin: true },
    { username: 'li.gong', name: '李工（本单位采样员）', unit_code: 'UNIT-A', is_admin: false },
    { username: 'wang.gong', name: '王工（本单位采样员）', unit_code: 'UNIT-A', is_admin: false },
    { username: 'partner.b', name: '协作单位账号', unit_code: 'UNIT-B', is_admin: false },
    { username: 'inspector.c', name: '督查员（第三方单位）', unit_code: 'UNIT-C', is_admin: false },
  ]
  if (session.token) await session.restore()
  window.addEventListener(SESSION_CLOSED_EVENT, (event) => {
    session.authError = (event as CustomEvent<string>).detail
  })
})
</script>

<style scoped>
.session-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.session-who {
  font-size: 13px;
}
.session-who em {
  color: var(--muted);
  font-style: normal;
  margin-left: 6px;
}
.muted {
  color: var(--muted);
}
.handover-form {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 100%;
  padding: 8px;
  border: 1px dashed var(--border);
  border-radius: 6px;
}
.handover-form input {
  width: 230px;
}
</style>
