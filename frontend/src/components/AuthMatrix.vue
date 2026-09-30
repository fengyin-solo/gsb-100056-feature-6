<template>
  <section class="matrix-panel">
    <header class="panel-head">
      <h3>授权矩阵（项目组—采样区—样品包）</h3>
      <button class="btn ghost" type="button" @click="reload">刷新</button>
    </header>
    <p class="panel-hint">
      只有区间所属单位的账号可调整授权；跨单位账号一律只读。项目组默认继承父级采样区权限，
      样品包可设 <code>readonly</code> 只读例外。
    </p>

    <table class="data-table">
      <thead>
        <tr>
          <th>层级</th>
          <th>区间</th>
          <th>所属单位</th>
          <th>已授权账号</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="`${row.kind}-${row.scope}`">
          <td>{{ row.kind === 'area' ? '采样区' : '样品包' }}</td>
          <td>
            {{ row.scope_name }}
            <span class="muted">（{{ row.scope }}<template v-if="row.kind === 'package'"> · {{ row.group_code }}</template>）</span>
          </td>
          <td>{{ row.owner_unit_name }}</td>
          <td>
            <span v-for="g in row.accounts" :key="g.grant_id" class="grant-chip"
                  :class="g.level">
              {{ g.account }} · {{ levelLabel(g.level) }}
              <button v-if="g.can_manage" class="link danger" type="button"
                      title="撤权（按操作时间定序）" @click="revoke(row, g)">
                撤权
              </button>
            </span>
            <span v-if="!row.accounts.length" class="muted">—</span>
          </td>
          <td>
            <template v-if="row.can_manage">
              <select v-model="drafts[`${row.kind}:${row.scope}`].account">
                <option value="" disabled>选择账号</option>
                <option v-for="acc in manageableAccounts" :key="acc.username"
                        :value="acc.username">
                  {{ acc.name }}
                </option>
              </select>
              <select v-model="drafts[`${row.kind}:${row.scope}`].level">
                <option value="readonly">只读</option>
                <option value="readwrite">读写</option>
              </select>
              <button class="btn primary" type="button" @click="applyGrant(row)">应用</button>
            </template>
            <span v-else class="readonly-tag">跨单位只读</span>
          </td>
        </tr>
      </tbody>
    </table>
    <footer class="panel-foot">
      <span v-if="message" :class="messageOk ? 'ok-text' : 'error-text'">{{ message }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type GrantRow = {
  grant_id: number
  account: string
  level: 'readonly' | 'readwrite'
  can_manage: boolean
}
type MatrixRow = {
  kind: 'area' | 'package'
  scope: string
  scope_name: string
  owner_unit: string
  owner_unit_name: string
  group_code?: string
  can_manage: boolean
  accounts: GrantRow[]
}
type AccountOption = { username: string; name: string; unit_code: string }

const emit = defineEmits<{ changed: [version: number] }>()

const session = useSessionStore()
const rows = ref<MatrixRow[]>([])
const accounts = ref<AccountOption[]>([])
const message = ref('')
const messageOk = ref(false)
const drafts = reactive<Record<string, { account: string; level: 'readonly' | 'readwrite' }>>({})

const manageableAccounts = ref<AccountOption[]>([])

function levelLabel(level: string) {
  return level === 'readwrite' ? '读写' : '只读'
}

async function reload() {
  const [matrixResp, catalogResp] = await Promise.all([
    request('/api/access/matrix'),
    request('/api/access/catalog'),
  ])
  if (!matrixResp.ok || !catalogResp.ok) return
  rows.value = (await matrixResp.json()).rows
  const catalog = await catalogResp.json()
  accounts.value = catalog.accounts
  for (const row of rows.value) {
    if (!drafts[`${row.kind}:${row.scope}`]) {
      drafts[`${row.kind}:${row.scope}`] = { account: '', level: 'readonly' }
    }
  }
  const unit = session.account?.unit_code
  manageableAccounts.value = accounts.value.filter((acc) => acc.unit_code === unit)
}

async function applyGrant(row: MatrixRow) {
  message.value = ''
  const draft = drafts[`${row.kind}:${row.scope}`]
  if (!draft.account) {
    messageOk.value = false
    message.value = '请先选择要授权的账号'
    return
  }
  const response = await request('/api/access/grants', {
    method: 'PUT',
    body: JSON.stringify({
      account: draft.account,
      kind: row.kind,
      scope: row.scope,
      level: draft.level,
    }),
  })
  const payload = await response.json()
  messageOk.value = response.ok
  message.value = response.ok
    ? `授权已调整${payload.backfilled_refs?.length ? `，回填历史共享引用 ${payload.backfilled_refs.length} 条` : ''}`
    : payload.detail ?? '授权调整失败'
  if (response.ok) {
    await reload()
    emit('changed', payload.decision_version as number)
  }
}

async function revoke(row: MatrixRow, grant: GrantRow) {
  message.value = ''
  const opTime = prompt(`撤权操作时间（ISO8601），将与调班按时间定序：`, new Date().toISOString())
  if (!opTime) return
  const response = await request(`/api/access/grants/${grant.grant_id}/revoke`, {
    method: 'POST',
    body: JSON.stringify({ op_time: opTime }),
  })
  const payload = await response.json()
  messageOk.value = response.ok
  message.value = response.ok
    ? `已撤权，关闭旧会话 ${payload.sessions_closed} 个`
    : payload.detail ?? '撤权失败'
  await reload()
  if (response.ok) emit('changed', payload.decision_version as number)
}

onMounted(reload)
defineExpose({ reload })
</script>

<style scoped>
.matrix-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin: 12px 0;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.panel-head h3 {
  margin: 0;
  font-size: 15px;
}
.panel-hint {
  font-size: 12px;
  color: var(--muted);
}
.muted {
  color: var(--muted);
  font-size: 12px;
}
.grant-chip {
  display: inline-block;
  margin: 2px 4px 2px 0;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  border: 1px solid var(--border);
}
.grant-chip.readonly {
  background: #f2f4f7;
}
.grant-chip.readwrite {
  background: #ecfdf3;
  border-color: #abefc6;
}
.danger {
  color: #b42318;
  margin-left: 6px;
}
.readonly-tag {
  font-size: 12px;
  color: var(--muted);
  border: 1px dashed var(--border);
  padding: 2px 8px;
  border-radius: 4px;
}
.panel-foot {
  margin-top: 8px;
  font-size: 12px;
}
.ok-text {
  color: #027a48;
}
select {
  margin-right: 4px;
}
</style>
