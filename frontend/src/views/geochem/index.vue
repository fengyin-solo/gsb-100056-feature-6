<template>
  <section class="page" data-module="geochem">
    <header class="page-head">
      <div>
        <h2>化探分析管理 · 三级权限</h2>
        <p class="page-desc">
          项目组—采样区—样品包三级归属；跨单位账号只读且样品类型等字段脱敏，
          越权改采样点位直接拒绝。台账、点位图、样品袋待办共用同一份授权快照。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" :disabled="!session.token" @click="openCreate">
          登记化探样品
        </button>
      </div>
    </header>

    <div v-if="session.authError" class="banner error">{{ session.authError }}</div>
    <div v-if="!session.account" class="banner muted">请先在右上角登录后查看化探数据。</div>

    <template v-if="session.account">
      <AccessViews ref="viewsRef" />
      <AuthMatrix @changed="onGrantChanged" />

      <form class="filter-bar" @submit.prevent="reload">
        <label class="filter-item">
          <span>样品编号</span>
          <input v-model="keyword" placeholder="按样品编号检索" />
        </label>
        <label class="filter-item">
          <span>状态</span>
          <select v-model="status">
            <option value="">全部</option>
            <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th>样品编号</th>
            <th>样品类型</th>
            <th>采样点位</th>
            <th>样品包</th>
            <th>分析元素</th>
            <th>状态</th>
            <th>点位/资料修改</th>
            <th>流转动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="String(row.id)">
            <td>{{ row['样品编号'] ?? '—' }}</td>
            <td>{{ row['样品类型'] ?? '—' }}</td>
            <td>{{ row['采样点位'] ?? '—' }}</td>
            <td>{{ row['样品包编号'] ?? '—' }}</td>
            <td>{{ row['分析元素'] ?? '—' }}</td>
            <td>{{ row['status'] }}</td>
            <td>
              <button class="link" type="button" @click="openEdit(row)">修改</button>
            </td>
            <td class="row-actions">
              <button v-for="action in actions" :key="action" class="link" type="button"
                      @click="runAction(action, row)">
                {{ action }}
              </button>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td :colspan="8" class="empty-state">当前账号在三级归属下没有可见样品</td>
          </tr>
        </tbody>
      </table>

      <div v-if="editing" class="edit-mask" @click.self="editing = null">
        <form class="edit-dialog" @submit.prevent="submitEdit">
          <h3>修改样品 {{ editing['样品编号'] }}</h3>
          <p class="panel-hint">
            当前归属：{{ editing['采样区编号'] }} / {{ editing['项目组编号'] }} /
            {{ editing['样品包编号'] }}。采样点位仅本单位读写账号可改，越权会被拒绝。
          </p>
          <label v-for="field in editableFields" :key="field">
            <span>{{ field }}</span>
            <input v-model="editForm[field]" :disabled="field === '采样点位' && !pointWritable" />
          </label>
          <div class="dialog-actions">
            <button class="btn" type="button" @click="editing = null">取消</button>
            <button class="btn primary" type="submit">保存</button>
          </div>
          <p v-if="editError" class="error-text">{{ editError }}</p>
        </form>
      </div>

      <footer class="page-foot">
        <span>共 {{ total }} 条可见化探样品（无授权样品不显示，跨单位行已脱敏）</span>
        <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      </footer>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'
import AccessViews from '@/components/AccessViews.vue'
import AuthMatrix from '@/components/AuthMatrix.vue'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, string | number | null> & {
  access?: { level: string; same_unit: boolean }
}

const ENDPOINT = '/api/geochem'
const actions = ['送样检测', '登记结果', '发起复检']
const statuses = ['待送样', '分析中', '已完成', '需复检']
const editableFields = ['样品类型', '采样点位', '分析元素', '检测方法', '检出限', '分析日期']

const session = useSessionStore()
const viewsRef = ref<InstanceType<typeof AccessViews> | null>(null)
const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const keyword = ref('')
const status = ref('')

const editing = ref<Row | null>(null)
const editForm = ref<Record<string, string>>({})
const editError = ref('')
const pointWritable = computed(
  () => editing.value?.access?.same_unit && editing.value?.access?.level === 'readwrite',
)

function openCreate() {
  errorMessage.value = '新样品登记请在样品包明细页发起（须指定样品包编号与本单位写权）'
}

function openEdit(row: Row) {
  editing.value = row
  editError.value = ''
  editForm.value = Object.fromEntries(
    editableFields.map((field) => [field, String(row[field] ?? '')]),
  )
}

async function submitEdit() {
  if (!editing.value) return
  editError.value = ''
  const response = await request(`${ENDPOINT}/${editing.value.id}`, {
    method: 'PATCH',
    body: JSON.stringify({ values: editForm.value }),
  })
  const payload = await response.json()
  if (!response.ok || !payload.ok) {
    editError.value = (!response.ok ? payload.detail : payload.message) ?? '修改被拒绝'
    return
  }
  editing.value = null
  await reload()
  await viewsRef.value?.reload()
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  const response = await request(`${ENDPOINT}/${row.id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  const payload = await response.json()
  if (!response.ok || !payload.ok) {
    errorMessage.value = (!response.ok ? payload.detail : payload.message) ?? '操作被拒绝'
    return
  }
  await reload()
  await viewsRef.value?.reload()
}

async function onGrantChanged() {
  await reload()
  await viewsRef.value?.reload()
}

async function reload() {
  errorMessage.value = ''
  if (!session.token) return
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (status.value) query.set('status', status.value)
  const response = await request(`${ENDPOINT}?${query.toString()}`)
  if (!response.ok) return
  const payload = await response.json()
  rows.value = payload.items ?? []
  total.value = payload.total ?? rows.value.length
}

onMounted(() => {
  if (session.token) void reload()
})
</script>

<style scoped>
.banner {
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 12px;
  font-size: 13px;
}
.banner.error {
  background: #fef3f2;
  border: 1px solid #fda29b;
  color: #b42318;
}
.banner.muted {
  background: #f2f4f7;
  color: var(--muted);
}
.edit-mask {
  position: fixed;
  inset: 0;
  background: rgb(16 24 40 / 45%);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.edit-dialog {
  background: #fff;
  border-radius: 8px;
  padding: 16px 20px;
  width: 420px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.edit-dialog h3 {
  margin: 0;
}
.edit-dialog label span {
  display: block;
  font-size: 12px;
  color: var(--muted);
}
.edit-dialog input {
  width: 100%;
}
.edit-dialog input:disabled {
  background: #f2f4f7;
}
.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.panel-hint {
  font-size: 12px;
  color: var(--muted);
  margin: 0;
}
</style>
