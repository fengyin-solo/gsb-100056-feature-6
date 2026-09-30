<template>
  <section class="page" data-module="geochem">
    <header class="page-head">
      <div>
        <h2>化探分析管理</h2>
        <p class="page-desc">
          样品归属“项目组—采样区—样品包”三级结构；当前账号
          <strong>{{ store.session?.account_name }}（{{ store.session?.unit_name }}）</strong>
          只能看到授权样品，只读样品上的动作与点位修改会被服务端拒绝。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出可见化探清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <p v-if="message" class="ok-banner">{{ message }}</p>
    <p v-if="errorMessage" class="error-banner">{{ errorMessage }}</p>

    <table class="data-table">
      <thead>
        <tr>
          <th>授权</th>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>采样点位修改</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td>
            <span class="access-badge" :class="String(row._access_level)">
              {{ accessLabel(row._access_level) }}
            </span>
          </td>
          <td v-for="column in columns" :key="column">
            {{ row[column] ?? (sensitiveColumns.includes(column) ? '******' : '—') }}
          </td>
          <td>
            <button
              v-if="row._can_write"
              class="link"
              type="button"
              @click="editPoint(row)"
            >
              改点位
            </button>
            <span v-else class="muted">只读</span>
          </td>
          <td class="row-actions">
            <template v-if="row._can_write">
              <button
                v-for="action in actions"
                :key="action"
                class="link"
                type="button"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </template>
            <span v-else class="muted">无写权限</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 3" class="empty-state">当前账号没有可见化探样品（无授权样品不会出现在列表中）</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条可见化探分析记录</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { ApiError, fetchJson, writeJson } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, string | number | boolean | null>

const store = useSessionStore()

const ENDPOINT = '/api/geochem'
const columns = ["样品编号", "样品类型", "采样点位", "分析元素", "检测方法", "检出限", "分析日期", "样品状态"]
// 跨单位只读时后端会移除这些字段，前端统一显示 ******，不暴露任何线索
const sensitiveColumns = ["样品类型", "分析元素", "检测方法", "检出限"]
const actions = ["送样检测", "登记结果", "发起复检"]
const stats = [{ label: "待送样样品", value: 0 }, { label: "分析中样品", value: 0 }, { label: "需复检样品", value: 0 }]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const message = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    const payload = await fetchJson<{ total: number }>(`${ENDPOINT}/export`)
    message.value = `已导出当前账号可见的 ${payload.total} 条样品（跨单位敏感字段已打码）`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

async function editPoint(row: Row) {
  const next = window.prompt('输入新的采样点位', String(row['采样点位'] ?? ''))
  if (next === null || !next.trim()) return
  errorMessage.value = ''
  message.value = ''
  try {
    await writeJson(`${ENDPOINT}/${row.id}/point`, { 采样点位: next.trim() }, 'PUT')
    message.value = `样品 ${row['样品编号']} 的采样点位已更新`
    await reload()
  } catch (error) {
    handleError(error)
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  message.value = ''
  try {
    await writeJson(`${ENDPOINT}/${row.id}/actions`, { action })
    message.value = `样品 ${row['样品编号']} 已执行「${action}」`
    await reload()
  } catch (error) {
    handleError(error)
  }
}

function handleError(error: unknown) {
  if (error instanceof ApiError) {
    if (error.code === 'point_write_denied' || error.code === 'cross_unit_readonly') {
      errorMessage.value = `越权被拒绝：${error.message}`
    } else {
      errorMessage.value = error.message
    }
  } else {
    errorMessage.value = error instanceof Error ? error.message : '化探分析操作失败'
  }
}

function accessLabel(level: unknown): string {
  return ({ read_write: '读写', read: '只读', none: '无权限' } as Record<string, string>)[String(level)] ?? '—'
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters.value)) {
    if (value) params.set(key, value)
  }
  try {
    const payload = await fetchJson<{ items: Row[]; total: number }>(`${ENDPOINT}?${params.toString()}`)
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats[0].value = rows.value.filter((r) => r.status === '待送样').length
    stats[1].value = rows.value.filter((r) => r.status === '分析中').length
    stats[2].value = rows.value.filter((r) => r.status === '需复检').length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '化探分析列表读取失败'
  }
}

onMounted(reload)
</script>
