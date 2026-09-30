<template>
  <section class="page" data-module="geochem-security">
    <header class="page-head">
      <div>
        <h2>化探样品授权矩阵</h2>
        <p class="page-desc">
          项目组—采样区—样品包三级归属。仅归属单位账号可调整区间，跨单位账号一律只读（封顶只读）；
          授权结论同步化探台账、点位图与样品袋追溯待办。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="reloadAll">刷新全部结论</button>
        <button class="btn" type="button" @click="runConsistency">三处同源校验</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">当前账号 / 单位</span>
        <strong class="stat-value">{{ identity }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">三处同源校验</span>
        <strong class="stat-value" :class="{ 'ok-text': consistency?.ok, 'error-text': consistency && !consistency.ok }">
          {{ consistency ? (consistency.ok ? '一致' : `异常 ${consistency.problems.length}`) : '未执行' }}
        </strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">可见样品 / 点位 / 待办</span>
        <strong class="stat-value">
          {{ ledgerItems.length }} / {{ mapItems.length }} / {{ bagItems.length }}
        </strong>
      </article>
    </div>

    <p v-if="message" class="ok-banner">{{ message }}</p>
    <p v-if="errorMessage" class="error-banner">{{ errorMessage }}</p>

    <!-- 新增授权区间 -->
    <form class="filter-bar" @submit.prevent="addGrant">
      <label class="filter-item">
        <span>账号</span>
        <select v-model="draft.account_id">
          <option v-for="acc in hierarchy?.accounts ?? []" :key="acc.id" :value="acc.id">
            {{ acc.name }} · {{ unitName(acc.unit_id) }}
          </option>
        </select>
      </label>
      <label class="filter-item">
        <span>层级</span>
        <select v-model="draft.node_kind">
          <option value="group">项目组</option>
          <option value="area">采样区</option>
          <option value="package">样品包</option>
        </select>
      </label>
      <label class="filter-item">
        <span>节点</span>
        <select v-model="draft.node_id">
          <option v-for="node in draftNodes" :key="node.id" :value="node.id">
            {{ node.name }}（{{ node.id }}）
          </option>
        </select>
      </label>
      <label class="filter-item">
        <span>授权档位</span>
        <select v-model="draft.level">
          <option value="read_write">读写</option>
          <option value="read">只读</option>
          <option value="none">无权限（收窄例外）</option>
        </select>
      </label>
      <button class="btn primary" type="submit">写入矩阵</button>
    </form>

    <h3>授权区间</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>账号</th><th>单位</th><th>节点</th><th>配置档位</th><th>生效结论</th><th>来源</th><th>版本</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in matrixItems" :key="String(row.grant_id)">
          <td>{{ row.account_name }}</td>
          <td>{{ row.account_unit_name }}</td>
          <td>{{ row.node_label }}</td>
          <td :class="{ muted: row.configured_level !== row.effective_level }">
            {{ levelLabel(row.configured_level) }}
            <span v-if="row.configured_level !== row.effective_level">（已封顶）</span>
          </td>
          <td>{{ levelLabel(row.effective_level) }}</td>
          <td class="muted">{{ row.effective_source }}</td>
          <td>v{{ row.version }}</td>
          <td class="row-actions">
            <template v-if="row.can_manage">
              <button class="link" type="button" @click="setLevel(row, 'read_write')">读写</button>
              <button class="link" type="button" @click="setLevel(row, 'read')">只读</button>
              <button class="link danger" type="button" @click="setLevel(row, 'none')">无权限</button>
              <button class="link danger" type="button" @click="removeGrant(row)">撤销区间</button>
            </template>
            <span v-else class="muted">跨单位只读</span>
          </td>
        </tr>
        <tr v-if="!matrixItems.length">
          <td colspan="8" class="empty-state">当前账号看不到任何授权区间</td>
        </tr>
      </tbody>
    </table>

    <div class="two-col">
      <section>
        <h3>调班 / 撤权（按操作时间定序）</h3>
        <form class="stack-form" @submit.prevent="reassign">
          <label class="filter-item">
            <span>目标账号</span>
            <select v-model="lifecycle.account_id">
              <option v-for="acc in hierarchy?.accounts ?? []" :key="acc.id" :value="acc.id">
                {{ acc.name }} · {{ unitName(acc.unit_id) }}{{ acc.active ? '' : '（已停用）' }}
              </option>
            </select>
          </label>
          <label class="filter-item">
            <span>调入单位</span>
            <select v-model="lifecycle.new_unit_id">
              <option v-for="u in hierarchy?.units ?? []" :key="u.id" :value="u.id">{{ u.name }}</option>
            </select>
          </label>
          <label class="filter-item">
            <span>操作时间（留空取当前）</span>
            <input v-model="lifecycle.op_at" placeholder="2026-09-30T10:00:00" />
          </label>
          <button class="btn primary" type="submit">执行调班</button>
          <button class="btn danger" type="button" @click="revoke">撤权停用</button>
        </form>
      </section>

      <section>
        <h3>同源投影快照</h3>
        <p class="muted small">
          台账、点位图、样品袋待办对每个样品包的结论来自同一计算结果；跨单位只读时样品类型打码。
        </p>
        <table class="data-table">
          <thead><tr><th>样品包</th><th>台账</th><th>点位图</th><th>样品袋</th></tr></thead>
          <tbody>
            <tr v-for="pkg in packageSummary" :key="pkg.id">
              <td>{{ pkg.name }}</td>
              <td :class="pkg.ledgerClass">{{ pkg.ledger }}</td>
              <td :class="pkg.mapClass">{{ pkg.map }}</td>
              <td :class="pkg.bagClass">{{ pkg.bag }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <h3>历史共享引用（调整后自动回填，原经手账号不变）</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>#</th><th>账号</th><th>引用节点</th><th>历史快照</th><th>当前结论</th>
          <th>原经手人</th><th>回填人</th><th>回填时间</th><th>已失效</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="ref in references" :key="ref.id" :class="{ staleRow: ref.stale }">
          <td>{{ ref.id }}</td>
          <td>{{ accountName(ref.account_id) }}</td>
          <td>{{ kindLabel(ref.node_kind) }} {{ ref.node_id }}</td>
          <td>{{ levelLabel(ref.snapshot_level) }}</td>
          <td>{{ ref.current_level ? levelLabel(ref.current_level) : '待回填' }}</td>
          <td>{{ accountName(ref.created_by) }}</td>
          <td>{{ ref.backfill_by ? accountName(ref.backfill_by) : '—' }}</td>
          <td>{{ ref.backfill_at ?? '—' }}</td>
          <td>{{ ref.stale ? '是' : '否' }}</td>
        </tr>
      </tbody>
    </table>

    <h3>权限审计轨迹</h3>
    <table class="data-table">
      <thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>节点</th><th>详情</th></tr></thead>
      <tbody>
        <tr v-for="log in auditItems" :key="log.id">
          <td>{{ log.at }}</td>
          <td>{{ log.operator_name }} · {{ log.operator_unit_id }}</td>
          <td>{{ log.action }}</td>
          <td>{{ kindLabel(log.node_kind) }} {{ log.node_id }}</td>
          <td class="muted small">{{ JSON.stringify(log.detail) }}</td>
        </tr>
        <tr v-if="!auditItems.length"><td colspan="5" class="empty-state">暂无审计记录</td></tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { fetchJson, request, writeJson } from '@/api/client'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()

type Level = 'none' | 'read' | 'read_write'
type MatrixRow = {
  grant_id: number
  account_id: string
  account_name: string
  account_unit_id: string
  account_unit_name: string
  node_kind: string
  node_id: string
  node_label: string
  configured_level: Level
  effective_level: Level
  effective_source: string
  version: number
  can_manage: boolean
}

const hierarchy = ref<Awaited<ReturnType<typeof loadHierarchy>> | null>(null)
const matrixItems = ref<MatrixRow[]>([])
const ledgerItems = ref<Record<string, unknown>[]>([])
const mapItems = ref<Record<string, unknown>[]>([])
const bagItems = ref<Record<string, unknown>[]>([])
type ReferenceRow = {
  id: number
  account_id: string
  node_kind: string
  node_id: string
  snapshot_level: string
  current_level: string | null
  created_by: string
  backfill_by: string | null
  backfill_at: string | null
  stale: boolean
}
type AuditRow = {
  id: number
  at: string
  operator_name: string
  operator_unit_id: string
  action: string
  node_kind: string
  node_id: string
  detail: Record<string, unknown>
}
const references = ref<ReferenceRow[]>([])
const auditItems = ref<AuditRow[]>([])
const consistency = ref<{ ok: boolean; problems: unknown[] } | null>(null)
const errorMessage = ref('')
const message = ref('')

const draft = reactive({ account_id: 'a-zhao', node_kind: 'area', node_id: 'A1', level: 'read' })
const lifecycle = reactive({ account_id: 'a-li', new_unit_id: 'U-B', op_at: '' })

const identity = computed(() =>
  store.session ? `${store.session.account_name} · ${store.session.unit_name}` : '未登录',
)

const draftNodes = computed(() => {
  if (!hierarchy.value) return []
  if (draft.node_kind === 'group') return hierarchy.value.groups
  if (draft.node_kind === 'area') return hierarchy.value.areas
  return hierarchy.value.packages
})

const packageSummary = computed(() => {
  const ledgerByPkg = new Map(ledgerItems.value.map((r) => [String(r.package_id), String(r._access_level)]))
  const mapByPkg = new Map(mapItems.value.map((r) => [String(r.package_id), String(r.level)]))
  const bagByPkg = new Map(bagItems.value.map((r) => [String(r.package_id), String(r.level)]))
  return (hierarchy.value?.packages ?? []).map((p) => {
    const ledger = ledgerByPkg.get(p.id) ?? ''
    const map = mapByPkg.get(p.id) ?? ''
    const bag = bagByPkg.get(p.id) ?? ''
    return {
      ...p,
      ledger: ledger ? levelLabel(ledger) : '不可见',
      map: map ? levelLabel(map) : '不可见',
      bag: bag ? levelLabel(bag) : '不可见',
      ledgerClass: ledger === 'read_write' ? 'ok-text' : ledger === 'read' ? 'warn-text' : 'muted',
      mapClass: map === 'read_write' ? 'ok-text' : map === 'read' ? 'warn-text' : 'muted',
      bagClass: bag === 'read_write' ? 'ok-text' : bag === 'read' ? 'warn-text' : 'muted',
    }
  })
})

async function loadHierarchy() {
  return fetchJson<{
    groups: { id: string; name: string }[]
    areas: { id: string; name: string }[]
    packages: { id: string; name: string }[]
    units: { id: string; name: string }[]
    accounts: { id: string; name: string; unit_id: string; active: boolean }[]
  }>('/api/security/hierarchy')
}

async function reloadAll() {
  errorMessage.value = ''
  message.value = ''
  try {
    const [h, matrix, ledger, pmap, bags, refs, audit] = await Promise.all([
      loadHierarchy(),
      fetchJson<{ items: MatrixRow[] }>('/api/security/matrix'),
      fetchJson<{ items: Record<string, unknown>[] }>('/api/security/projections/ledger'),
      fetchJson<{ items: Record<string, unknown>[] }>('/api/security/projections/map'),
      fetchJson<{ items: Record<string, unknown>[] }>('/api/security/projections/bag-todo'),
      fetchJson<{ items: Record<string, unknown>[] }>('/api/security/references'),
      fetchJson<{ items: Record<string, unknown>[] }>('/api/security/audit'),
    ])
    hierarchy.value = h
    matrixItems.value = matrix.items
    ledgerItems.value = ledger.items
    mapItems.value = pmap.items
    bagItems.value = bags.items
    references.value = refs.items as ReferenceRow[]
    auditItems.value = audit.items as AuditRow[]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '授权数据加载失败'
  }
}

async function runConsistency() {
  errorMessage.value = ''
  try {
    const result = await fetchJson<{ ok: boolean; problems: unknown[] }>('/api/security/projections/consistency')
    consistency.value = result
    if (result.ok) {
      message.value = '三处结论同源一致（全部样品包核对通过）'
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '一致性校验失败'
  }
}

async function addGrant() {
  errorMessage.value = ''
  try {
    const result = await writeJson<{ references_backfilled: number }>('/api/security/matrix', {
      account_id: draft.account_id,
      node_kind: draft.node_kind,
      node_id: draft.node_id,
      level: draft.level,
    }, 'PUT')
    message.value = `矩阵已更新，历史共享引用回填 ${result.references_backfilled} 条，相关缓存已清理`
    await reloadAll()
  } catch (error) {
    errorMessage.value = friendly(error)
  }
}

async function setLevel(row: MatrixRow, level: Level) {
  errorMessage.value = ''
  try {
    await writeJson('/api/security/matrix', {
      account_id: row.account_id,
      node_kind: row.node_kind,
      node_id: row.node_id,
      level,
      expected_version: row.version,
    }, 'PUT')
    message.value = `${row.node_label} 的授权已调整为「${levelLabel(level)}」`
    await reloadAll()
  } catch (error) {
    errorMessage.value = friendly(error)
  }
}

async function removeGrant(row: MatrixRow) {
  errorMessage.value = ''
  try {
    await writeJson('/api/security/matrix', {
      account_id: row.account_id,
      node_kind: row.node_kind,
      node_id: row.node_id,
      level: row.effective_level,
      expected_version: row.version,
      delete: true,
    }, 'PUT')
    message.value = `${row.node_label} 的授权区间已撤销`
    await reloadAll()
  } catch (error) {
    errorMessage.value = friendly(error)
  }
}

async function reassign() {
  errorMessage.value = ''
  try {
    const result = await writeJson<{ message: string }>('/api/security/reassign', {
      account_id: lifecycle.account_id,
      new_unit_id: lifecycle.new_unit_id,
      op_at: lifecycle.op_at || null,
    })
    message.value = result.message
    await reloadAll()
  } catch (error) {
    errorMessage.value = friendly(error)
  }
}

async function revoke() {
  errorMessage.value = ''
  try {
    const result = await writeJson<{ message: string }>('/api/security/revoke', {
      account_id: lifecycle.account_id,
      op_at: lifecycle.op_at || null,
    })
    message.value = result.message
    await reloadAll()
  } catch (error) {
    errorMessage.value = friendly(error)
  }
}

// 登录态可能被顶号：401 时刷新页面状态
void request
function friendly(error: unknown): string {
  const text = error instanceof Error ? error.message : '操作失败'
  return text
}

function unitName(unitId: string): string {
  return hierarchy.value?.units.find((u) => u.id === unitId)?.name ?? unitId
}
function accountName(id: string): string {
  const acc = hierarchy.value?.accounts.find((a) => a.id === id)
  return acc ? `${acc.name}（${id}）` : id
}
function levelLabel(level: string): string {
  return ({ read_write: '读写', read: '只读', none: '无权限' } as Record<string, string>)[level] ?? level
}
function kindLabel(kind: string): string {
  return ({ group: '项目组', area: '采样区', package: '样品包' } as Record<string, string>)[kind] ?? kind
}

onMounted(reloadAll)
</script>
