<template>
  <section class="page" data-module="snapshot">
    <header class="page-head">
      <div>
        <h2>表快照管理</h2>
        <p class="page-desc">
          维护快照的版本、保留策略与创建方式；保留策略按策略纪元增量批算，发布可恢复快照数据集。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记快照记录</button>
        <button class="btn" type="button" @click="exportRows">导出表快照管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section class="panel" data-panel="retention">
      <header class="panel-head">
        <h3>保留策略与策略纪元</h3>
        <span class="panel-hint">阈值变化后旧纪元结果不会被继续当作当前结果</span>
      </header>
      <div class="filter-bar">
        <label class="filter-item">
          <span>保留天数</span>
          <input v-model.number="retentionDraft" type="number" min="1" />
        </label>
        <label class="filter-item">
          <span>批算策略</span>
          <select v-model="batchStrategy">
            <option value="defer">顺延下一纪元（defer）</option>
            <option value="recompute">立即按新阈值重算（recompute）</option>
          </select>
        </label>
        <button class="btn" type="button" @click="saveRetention">调整保留策略</button>
        <button class="btn primary" type="button" :disabled="batchRunning" @click="runBatch">
          {{ batchRunning ? '批算中…' : '执行增量批算' }}
        </button>
        <button class="btn" type="button" @click="verifyAudit">核对可恢复快照数据集</button>
        <button class="btn ghost" type="button" @click="loadAudit">刷新审计清单</button>
      </div>
      <dl class="meta-grid">
        <div><dt>本轮策略纪元</dt><dd>{{ retention.epoch ?? '—' }}</dd></div>
        <div><dt>当前保留天数</dt><dd>{{ retention.retention_days ?? '—' }} 天</dd></div>
        <div><dt>可恢复数据集版本</dt><dd>{{ retention.latest_dataset_epoch ?? '—' }}</dd></div>
        <div><dt>策略修订序号</dt><dd>{{ retention.revision_seq ?? '—' }}</dd></div>
        <div><dt>受保护快照</dt><dd>{{ protectedCount }} 个</dd></div>
        <div><dt>清理候选</dt><dd>{{ candidateCount }} 个</dd></div>
      </dl>
      <p v-if="retention.stale" class="stale-text">
        当前可恢复快照数据集基于旧保留阈值，下一纪元将按
        {{ retention.pending?.next_retention_days ?? retention.retention_days }} 天补算；旧纪元结果不会当作当前结果。
      </p>
      <p v-if="batchMessage" class="panel-hint">{{ batchMessage }}</p>
      <p v-if="retentionMessage" class="panel-hint">{{ retentionMessage }}</p>
    </section>

    <section class="panel" data-panel="dataset">
      <header class="panel-head">
        <h3>可恢复快照数据集（策略纪元 {{ dataset?.epoch ?? '—' }}）</h3>
        <span class="panel-hint">恢复链与分支引用始终受保护</span>
      </header>
      <div class="split">
        <div>
          <h4 class="split-title">受保护快照</h4>
          <table class="data-table">
            <thead>
              <tr><th>快照编号</th><th>表名称</th><th>快照时间</th><th>受保护原因</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in dataset?.protected ?? []" :key="item.快照编号">
                <td>{{ item.快照编号 }}</td>
                <td>{{ item.表名称 }}</td>
                <td>{{ item.快照时间 }}</td>
                <td>{{ item.受保护原因 }}</td>
              </tr>
              <tr v-if="!dataset?.protected?.length">
                <td colspan="4" class="empty-state">当前纪元没有受保护快照</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div>
          <h4 class="split-title">清理候选（{{ dataset?.candidates?.length ?? 0 }}）</h4>
          <table class="data-table">
            <thead>
              <tr><th>快照编号</th><th>表名称</th><th>快照时间</th><th>保留天数</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in (dataset?.candidates ?? []).slice(0, 8)" :key="item.快照编号">
                <td>{{ item.快照编号 }}</td>
                <td>{{ item.表名称 }}</td>
                <td>{{ item.快照时间 }}</td>
                <td>{{ item.保留天数 }}</td>
              </tr>
              <tr v-if="!dataset?.candidates?.length">
                <td colspan="4" class="empty-state">当前纪元没有清理候选</td>
              </tr>
            </tbody>
          </table>
          <p v-if="(dataset?.candidates?.length ?? 0) > 8" class="panel-hint">
            仅展示前 8 个，共 {{ dataset?.candidates?.length }} 个。
          </p>
        </div>
      </div>
    </section>

    <section class="panel" data-panel="audit">
      <header class="panel-head">
        <h3>快照保留审计清单</h3>
        <span class="panel-hint">与数据回滚恢复入口指向同一批结果</span>
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
            <td colspan="7" class="empty-state">暂无审计记录，先执行一次增量批算</td>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无表快照管理数据，可先登记快照记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条表快照记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson, request } from '@/api/client'

type Row = Record<string, string | number | null>
type DatasetItem = { 快照编号: string; 表名称?: string; 快照时间?: string; 保留天数?: number; 受保护原因?: string }
type Dataset = {
  epoch: number
  retention_days: number
  protected: DatasetItem[]
  candidates: DatasetItem[]
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
type Retention = {
  epoch?: number
  retention_days?: number
  latest_dataset_epoch?: number
  revision_seq?: number
  stale?: boolean
  pending?: { retention_days?: number; next_retention_days?: number; deferred?: boolean } | null
}

const ENDPOINT = '/api/snapshot'
const columns = ["快照编号", "表名称", "快照时间", "快照版本", "快照大小", "保留天数", "创建方式", "快照状态"]
const actions = ["创建快照", "恢复快照", "清理快照"]
const statuses = ["已创建", "保留中", "已过期", "已清理"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const retention = ref<Retention>({})
const retentionDraft = ref(30)
const retentionMessage = ref('')
const batchStrategy = ref<'defer' | 'recompute'>('defer')
const batchRunning = ref(false)
const batchMessage = ref('')
const dataset = ref<Dataset | null>(null)
const auditItems = ref<AuditEntry[]>([])

const protectedCount = computed(() => dataset.value?.protected?.length ?? 0)
const candidateCount = computed(() => dataset.value?.candidates?.length ?? 0)

const stats = computed(() => [
  { label: '快照记录总数', value: total.value },
  { label: '受保护快照', value: protectedCount.value },
  { label: '清理候选', value: candidateCount.value },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '快照记录登记入口尚未接入审批流'
}

async function loadRetention() {
  try {
    const payload = await fetchJson<Retention & { revision_seq: number }>(`${ENDPOINT}/retention`)
    retention.value = payload
    retentionDraft.value = Number(payload.retention_days ?? 30)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保留策略读取失败'
  }
}

async function loadDataset() {
  try {
    const payload = await fetchJson<{ ok: boolean; dataset: Dataset | null; message?: string; stale?: boolean }>(`${ENDPOINT}/dataset`)
    dataset.value = payload.dataset
  } catch (error) {
    dataset.value = null
    errorMessage.value = error instanceof Error ? error.message : '可恢复快照数据集读取失败'
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

async function saveRetention() {
  retentionMessage.value = ''
  try {
    const payload = await fetchJson<{ ok: boolean; message: string }>(`${ENDPOINT}/retention`, {
      method: 'PUT',
      body: JSON.stringify({ retention_days: Number(retentionDraft.value) }),
    })
    retentionMessage.value = payload.message
    await loadRetention()
  } catch (error) {
    retentionMessage.value = error instanceof Error ? error.message : '保留策略调整失败'
  }
}

async function runBatch() {
  batchRunning.value = true
  batchMessage.value = ''
  try {
    const payload = await fetchJson<{ ok: boolean; published: boolean; message: string }>(`${ENDPOINT}/batch`, {
      method: 'POST',
      body: JSON.stringify({ values: { strategy: batchStrategy.value } }),
    })
    batchMessage.value = payload.message
    await Promise.all([loadRetention(), loadDataset(), loadAudit()])
  } catch (error) {
    batchMessage.value = error instanceof Error ? error.message : '增量批算失败'
  } finally {
    batchRunning.value = false
  }
}

async function verifyAudit() {
  try {
    const payload = await fetchJson<{ verified: boolean; conclusion: string }>(`${ENDPOINT}/audit/verify`, {
      method: 'POST',
      body: JSON.stringify({ values: {} }),
    })
    batchMessage.value = payload.conclusion
    await loadAudit()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '核对失败'
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
      throw new Error('表快照管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '表快照管理操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('快照记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '表快照管理列表读取失败'
  }
}

onMounted(async () => {
  await Promise.all([reload(), loadRetention(), loadDataset(), loadAudit()])
})
</script>
