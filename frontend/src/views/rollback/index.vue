<template>
  <section class="page" data-module="rollback">
    <header class="page-head">
      <div>
        <h2>数据回滚恢复管理</h2>
        <p class="page-desc">
          维护回滚记录的版本追踪与恢复计时；回滚清单消费同一份可恢复快照数据集，按策略纪元解释受保护版本。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记回滚记录</button>
        <button class="btn" type="button" @click="exportRows">导出数据回滚恢复清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section class="panel" data-panel="dataset">
      <header class="panel-head">
        <h3>可恢复快照数据集（策略纪元 {{ dataset?.epoch ?? '—' }}）</h3>
        <span class="panel-hint">与表快照管理读取同一份数据集</span>
      </header>
      <div class="filter-bar">
        <label class="filter-item">
          <span>策略纪元</span>
          <input v-model="epochDraft" type="number" min="1" placeholder="缺省为当前纪元" />
        </label>
        <button class="btn" type="button" @click="loadDataset">读取该纪元</button>
        <button class="btn primary" type="button" @click="verifyAudit">比对当前可恢复快照数据集</button>
        <button class="btn ghost" type="button" @click="loadAudit">刷新审计清单</button>
      </div>
      <p v-if="dataset?.stale" class="stale-text">
        该纪元基于旧保留阈值，等待下一纪元补算；回滚清单只接收完整策略版本的结果。
      </p>
      <p v-if="datasetMessage" class="panel-hint">{{ datasetMessage }}</p>
      <table class="data-table">
        <thead>
          <tr><th>快照编号</th><th>表名称</th><th>受保护原因</th><th>快照状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in dataset?.protected ?? []" :key="item.快照编号">
            <td>{{ item.快照编号 }}</td>
            <td>{{ item.表名称 }}</td>
            <td>{{ item.受保护原因 }}</td>
            <td>{{ item.快照状态 ?? '—' }}</td>
          </tr>
          <tr v-if="!dataset?.protected?.length">
            <td colspan="4" class="empty-state">该纪元没有解释出受保护快照</td>
          </tr>
        </tbody>
      </table>
      <h4 class="split-title">回滚记录引用</h4>
      <table class="data-table">
        <thead>
          <tr><th>记录编号</th><th>表名称</th><th>回滚版本</th><th>回滚状态</th><th>受保护</th><th>策略纪元</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in dataset?.rollback ?? []" :key="String(item.记录编号)">
            <td>{{ item.记录编号 }}</td>
            <td>{{ item.表名称 }}</td>
            <td>{{ item.回滚版本 }}</td>
            <td>{{ item.回滚状态 ?? '—' }}</td>
            <td>{{ item.受保护 ? '是' : '否' }}</td>
            <td>{{ item.策略纪元 ?? '—' }}</td>
          </tr>
          <tr v-if="!dataset?.rollback?.length">
            <td colspan="6" class="empty-state">暂无回滚记录</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel" data-panel="audit">
      <header class="panel-head">
        <h3>快照保留审计清单</h3>
        <span class="panel-hint">与表快照列表核对入口指向同一批结果</span>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th>策略纪元</th><th>保留天数</th><th>受保护</th><th>清理候选</th>
            <th>差异</th><th>核对结论</th><th>核对时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in auditItems" :key="String(item.epoch)">
            <td>{{ item.epoch }}</td>
            <td>{{ item.retention_days }}</td>
            <td>{{ (item.protected_versions ?? []).length }}</td>
            <td>{{ (item.candidate_versions ?? []).length }}</td>
            <td>{{ item.summary ?? '—' }}</td>
            <td>
              <span v-if="item.conclusion">{{ item.conclusion }}</span>
              <span v-else class="panel-hint">尚未核对</span>
            </td>
            <td>{{ item.checked_at ?? '—' }}</td>
          </tr>
          <tr v-if="!auditItems.length">
            <td colspan="7" class="empty-state">暂无审计记录，先到表快照管理执行一次增量批算</td>
          </tr>
        </tbody>
      </table>
    </section>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无数据回滚恢复数据，可先登记回滚记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据回滚恢复记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson, request } from '@/api/client'

type Row = Record<string, string | number | null>
type ProtectedItem = { 快照编号: string; 表名称?: string; 受保护原因?: string; 快照状态?: string }
type RollbackItem = {
  记录编号: string
  表名称?: string
  回滚版本?: string
  回滚状态?: string
  受保护?: boolean
  策略纪元?: number
}
type Dataset = {
  epoch: number
  retention_days: number
  stale?: boolean
  protected: ProtectedItem[]
  rollback: RollbackItem[]
}
type AuditEntry = {
  epoch: number
  retention_days: number
  protected_versions?: string[]
  candidate_versions?: string[]
  summary?: string
  conclusion?: string
  checked_at?: string
}

const ENDPOINT = '/api/rollback'
const columns = ["记录编号", "表名称", "回滚版本", "回滚原因", "影响范围", "恢复耗时", "回滚时间", "回滚状态"]
const actions = ["执行回滚", "确认恢复", "归档记录"]
const statuses = ["待回滚", "回滚中", "已回滚", "已恢复"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const dataset = ref<Dataset | null>(null)
const datasetMessage = ref('')
const epochDraft = ref<string>('')
const auditItems = ref<AuditEntry[]>([])

const stats = computed(() => [
  { label: '回滚记录总数', value: total.value },
  { label: '受保护快照', value: dataset.value?.protected?.length ?? 0 },
  { label: '当前策略纪元', value: dataset.value?.epoch ?? '—' },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '回滚记录登记入口尚未接入审批流'
}

async function loadDataset() {
  datasetMessage.value = ''
  const epoch = epochDraft.value ? `?epoch=${encodeURIComponent(epochDraft.value)}` : ''
  try {
    const payload = await fetchJson<{ ok: boolean; message: string } & Dataset>(`${ENDPOINT}/dataset${epoch}`)
    if (!payload.ok) {
      dataset.value = null
      datasetMessage.value = payload.message
      return
    }
    dataset.value = payload
  } catch (error) {
    dataset.value = null
    datasetMessage.value = error instanceof Error ? error.message : '可恢复快照数据集读取失败'
  }
}

async function loadAudit() {
  try {
    const payload = await fetchJson<{ items: AuditEntry[] }>(`${ENDPOINT}/audit`)
    auditItems.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '快照保留审计清单读取失败'
  }
}

async function verifyAudit() {
  const values = epochDraft.value ? { epoch: Number(epochDraft.value) } : {}
  try {
    const payload = await fetchJson<{ verified: boolean; conclusion: string }>(`${ENDPOINT}/audit/verify`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    datasetMessage.value = payload.conclusion
    await Promise.all([loadDataset(), loadAudit()])
  } catch (error) {
    datasetMessage.value = error instanceof Error ? error.message : '比对失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('数据回滚恢复动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据回滚恢复操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('回滚记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据回滚恢复列表读取失败'
  }
}

onMounted(async () => {
  await Promise.all([reload(), loadDataset(), loadAudit()])
})
</script>
