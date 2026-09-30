<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">地质勘探数据管理平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向地质勘探的钻孔编录、岩心取样、物探数据、化探分析、测绘资料与储量估算的综合数据管理后台。</span>
        <span class="head-user">
          当前值班：{{ store.operator }}（{{ store.unitName }}） · {{ store.shiftLabel }}
        </span>
        <label class="account-switch">
          切换账号
          <select :value="store.session?.account_id ?? ''" @change="switchAccount">
            <option value="" disabled>未登录</option>
            <option v-for="acc in accounts" :key="acc.id" :value="acc.id">
              {{ acc.name }} · {{ acc.unitName }}（{{ acc.id }}）
            </option>
          </select>
        </label>
      </header>
      <p v-if="loginError" class="error-banner">{{ loginError }}</p>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { login } from '@/api/client'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const loginError = ref('')

const accounts = [
  { id: 'a-fang', name: '方研', unitName: '甲地质大队' },
  { id: 'a-li', name: '李勘', unitName: '甲地质大队' },
  { id: 'a-zhao', name: '赵测', unitName: '乙检测中心' },
  { id: 'a-qian', name: '钱检', unitName: '乙检测中心' },
]

const navItems = [{ label: "运营概览", path: "/" }, { label: "化探分析", path: "/geochem" }, { label: "化探授权", path: "/geochem_security" }, { label: "钻孔编录", path: "/borehole" }, { label: "岩心管理", path: "/core" }, { label: "地层划分", path: "/stratigraphy" }, { label: "地球物理", path: "/geophysics" }, { label: "化验数据", path: "/assay" }, { label: "地质填图", path: "/mapping" }, { label: "测绘控制", path: "/survey_point" }, { label: "钻探日志", path: "/drilling_log" }, { label: "储量估算", path: "/reserve" }, { label: "样品登记", path: "/sample_registry" }, { label: "勘探设备", path: "/equipment" }, { label: "水文地质", path: "/hydro" }, { label: "剖面编录", path: "/section" }, { label: "地质报告", path: "/geological_report" }, { label: "遥感解译", path: "/remote" }, { label: "矿产评价", path: "/mineral" }, { label: "环境地质", path: "/environmental" }]

onMounted(async () => {
  store.restore()
  if (!store.session) {
    // 默认以本单位管理员身份进入，保证页面开箱可用
    await switchTo('a-fang')
  }
})

async function switchTo(accountId: string) {
  loginError.value = ''
  try {
    await login(accountId)
  } catch (error) {
    loginError.value = error instanceof Error ? error.message : '建立会话失败'
  }
}

function switchAccount(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (value) void switchTo(value)
}
</script>
