<template>
  <section class="projection-panel">
    <header class="panel-head">
      <h3>授权结论同步（三处同源）</h3>
      <span class="version-tag">decision_version = {{ version }} · 生成于 {{ generatedAt }}</span>
    </header>
    <nav class="tab-row">
      <button v-for="tab in tabs" :key="tab.key" class="btn" type="button"
              :class="{ primary: active === tab.key }" @click="active = tab.key">
        {{ tab.label }}
      </button>
      <button class="btn ghost" type="button" @click="reload">重新拉取同源快照</button>
    </nav>

    <table v-if="active === 'ledger'" class="data-table">
      <thead>
        <tr>
          <th>样品编号</th><th>样品类型</th><th>采样点位</th>
          <th>样品包</th><th>项目组</th><th>状态</th><th>授权来源</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in snapshot.ledger" :key="row.样品编号">
          <td>{{ row.样品编号 }}</td>
          <td>{{ row.样品类型 }}</td>
          <td>{{ row.采样点位 }}</td>
          <td>{{ row.package_name }}</td>
          <td>{{ row.group_name }}</td>
          <td>{{ row.status }}</td>
          <td>{{ viaText(row.access) }}</td>
        </tr>
      </tbody>
    </table>

    <div v-else-if="active === 'point_map'" class="point-grid">
      <article v-for="point in snapshot.point_map" :key="point.样品编号" class="point-card"
               :class="point.access.same_unit ? 'own' : 'cross'">
        <strong>{{ point.样品编号 }}</strong>
        <span>{{ point.采样点位 }}</span>
        <em>{{ point.package_code }} · {{ point.status }}</em>
        <small>{{ viaText(point.access) }}</small>
      </article>
      <p v-if="!snapshot.point_map.length" class="empty-state">点位图无可见图元</p>
    </div>

    <table v-else class="data-table">
      <thead>
        <tr>
          <th>样品包</th><th>所属项目组</th><th>样品总数</th>
          <th>待追溯数</th><th>待办样品</th><th>包权限</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="bag in snapshot.bag_todos" :key="bag.package_code">
          <td>{{ bag.package_name }}（{{ bag.package_code }}）</td>
          <td>{{ bag.group_name }}</td>
          <td>{{ bag.total }}</td>
          <td>{{ bag.pending }}</td>
          <td>
            <span v-for="todo in bag.todo" :key="todo.样品编号" class="todo-chip">
              {{ todo.样品编号 }}@{{ todo.采样点位 }}
            </span>
            <span v-if="!bag.todo.length" class="muted">无待办</span>
          </td>
          <td>{{ viaText(bag.access) }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Access = { level: string; same_unit: boolean; via: string }
type LedgerRow = {
  样品编号: string
  样品类型: string
  采样点位: string
  status: string
  package_name: string
  group_name: string
  access: Access
  [key: string]: unknown
}
type PointRow = {
  样品编号: string
  采样点位: string
  package_code: string
  status: string
  access: Access
}
type BagTodo = { 样品编号: string; 采样点位: string; 样品类型: string; status: string }
type BagRow = {
  package_code: string
  package_name: string
  group_name: string
  total: number
  pending: number
  todo: BagTodo[]
  access: Access
}
type Snapshot = {
  decision_version: number
  generated_at: string
  ledger: LedgerRow[]
  point_map: PointRow[]
  bag_todos: BagRow[]
}

const tabs = [
  { key: 'ledger', label: '化探台账' },
  { key: 'point_map', label: '点位图' },
  { key: 'bag_todos', label: '样品袋追溯待办' },
] as const

const active = ref<(typeof tabs)[number]['key']>('ledger')
const version = ref(0)
const generatedAt = ref('')
const snapshot = ref<Snapshot>({
  decision_version: 0,
  generated_at: '',
  ledger: [],
  point_map: [],
  bag_todos: [],
})

function viaText(access: Access) {
  const level = access.level === 'readwrite' ? '读写' : '只读'
  const scope = access.same_unit ? '本单位' : '跨单位'
  const via: Record<string, string> = {
    unit_admin: '单位管理员',
    group_inherits_area: '项目组继承采样区',
    package_grant: '样品包授权',
    package_exception: '样品包只读例外',
    cross_unit: '跨单位只读（敏感字段脱敏）',
  }
  return `${scope}${level} · ${via[access.via] ?? access.via}`
}

async function reload() {
  const response = await request('/api/geochem/access-view')
  if (!response.ok) return
  snapshot.value = await response.json()
  version.value = snapshot.value.decision_version
  generatedAt.value = snapshot.value.generated_at
}

onMounted(reload)
defineExpose({ reload })
</script>

<style scoped>
.projection-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin: 12px 0;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.panel-head h3 {
  margin: 0;
  font-size: 15px;
}
.version-tag {
  font-size: 12px;
  color: var(--muted);
}
.tab-row {
  display: flex;
  gap: 8px;
  margin: 8px 0;
}
.point-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.point-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  border: 1px solid var(--border);
  border-left-width: 4px;
  border-radius: 6px;
  padding: 8px 12px;
  min-width: 180px;
  background: #fff;
}
.point-card.own {
  border-left-color: #027a48;
}
.point-card.cross {
  border-left-color: #d92d20;
  background: #fffbfa;
}
.point-card em,
.point-card small {
  color: var(--muted);
  font-style: normal;
  font-size: 12px;
}
.todo-chip {
  display: inline-block;
  font-size: 12px;
  background: #f2f4f7;
  border-radius: 4px;
  padding: 1px 6px;
  margin: 1px 4px 1px 0;
}
.muted {
  color: var(--muted);
}
</style>
