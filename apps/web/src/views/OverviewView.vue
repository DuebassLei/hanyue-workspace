<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'
import StatusTag from '../components/StatusTag.vue'

const data = ref(null)
const loading = ref(true)
const error = ref('')

const publishPercent = computed(() => {
  const progress = data.value?.publishProgress
  if (!progress?.planned) return 0
  return Math.min(100, Math.round((progress.ratio || 0) * 100))
})

const publishLabel = computed(() => {
  const progress = data.value?.publishProgress
  if (!progress?.planned) return '本周还没有排期'
  return `${progress.published} / ${progress.planned} 已发布`
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.get('/dashboard')
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

onMounted(load)

const fmt = (value) => (value === null || value === undefined ? '—' : value)
</script>

<template>
  <div class="page overview-page">
    <div class="overview-head">
      <div>
        <span class="eyebrow">WORKSPACE OVERVIEW</span>
        <h1 class="page-title">创作总览</h1>
        <p class="page-sub">本周内容运营状态，先看重点，再继续行动。</p>
      </div>
      <div class="overview-actions">
        <button class="btn btn-ghost btn-sm" :disabled="loading" @click="load">
          <span aria-hidden="true">↻</span>
          刷新数据
        </button>
        <RouterLink class="btn btn-sm" :to="{ name: 'studio' }">＋ 新建稿件</RouterLink>
      </div>
    </div>

    <div v-if="error" class="banner banner-danger" role="alert">
      <span>加载失败：{{ error }}</span>
      <button class="btn btn-ghost btn-sm" @click="load">重试</button>
    </div>

    <div v-if="loading" class="overview-skeleton">
      <div v-for="i in 4" :key="i" class="skeleton" style="height: 118px" />
      <div class="skeleton" style="height: 300px" />
    </div>

    <template v-else-if="data">
      <section class="metric-grid overview-metrics" aria-label="核心指标">
        <RouterLink class="metric metric-link metric-featured" :to="{ name: 'calendar' }">
          <span class="metric-label">本周发布进度</span>
          <span class="metric-value">{{ data.publishProgress.published }}<small> / {{ data.publishProgress.planned }}</small></span>
          <span class="metric-track"><span :style="{ width: `${publishPercent}%` }" /></span>
          <span class="metric-hint">{{ publishLabel }}</span>
        </RouterLink>
        <RouterLink class="metric metric-link" :to="{ name: 'studio' }">
          <span class="metric-label">待审核内容</span>
          <span class="metric-value">{{ data.pendingReview }}</span>
          <span class="metric-hint">发布前需要人工确认 <span class="metric-arrow">→</span></span>
        </RouterLink>
        <RouterLink class="metric metric-link metric-alert" :to="{ name: 'publishing' }">
          <span class="metric-label">发布失败 / 需核实</span>
          <span class="metric-value">{{ data.failedJobs }}</span>
          <span class="metric-hint">不会自动重发，需先核实 <span class="metric-arrow">→</span></span>
        </RouterLink>
        <RouterLink class="metric metric-link" :to="{ name: 'publishing' }">
          <span class="metric-label">待同步账号</span>
          <span class="metric-value">{{ data.unsyncedAccounts }}</span>
          <span class="metric-hint">绑定但尚未成功同步 <span class="metric-arrow">→</span></span>
        </RouterLink>
      </section>

      <div class="overview-grid">
        <section class="card overview-card platform-card">
          <div class="section-heading">
            <div>
              <span class="eyebrow">DISTRIBUTION</span>
              <h2 class="card-title">平台连接状态</h2>
            </div>
            <RouterLink class="text-link" :to="{ name: 'publishing' }">管理账号 <span>→</span></RouterLink>
          </div>
          <div v-if="!data.platformStatus.length" class="empty compact-empty">
            <span class="empty-icon">＋</span>
            <span class="empty-title">还没有绑定平台账号</span>
            <span>绑定后可以查看授权状态与自动发布开关。</span>
            <RouterLink class="btn btn-sm" :to="{ name: 'publishing' }">去绑定账号</RouterLink>
          </div>
          <div v-else class="platform-list">
            <div v-for="item in data.platformStatus" :key="item.platform" class="platform-row">
              <div class="platform-mark">{{ item.label.slice(0, 1) }}</div>
              <div class="list-main">
                <div class="list-title">{{ item.label }}</div>
                <div class="list-meta">
                  自动发布：{{ item.autoPublishEnabled ? '已开启' : '未开启' }} · 最近同步：{{ item.lastSyncedAt ? item.lastSyncedAt.slice(0, 16).replace('T', ' ') : '尚未同步' }}
                </div>
              </div>
              <StatusTag :status="item.status" />
            </div>
          </div>
        </section>

        <section class="card overview-card recent-card">
          <div class="section-heading">
            <div>
              <span class="eyebrow">RECENT WORK</span>
              <h2 class="card-title">最近稿件</h2>
            </div>
            <RouterLink class="text-link" :to="{ name: 'studio' }">查看全部 <span>→</span></RouterLink>
          </div>
          <div v-if="!data.recentContent.length" class="empty compact-empty">
            <span class="empty-icon">✎</span>
            <span class="empty-title">还没有草稿</span>
            <span>从一个想法开始，直接让 AI 生成初稿。</span>
            <RouterLink class="btn btn-sm" :to="{ name: 'studio' }">新建稿件</RouterLink>
          </div>
          <div v-else class="recent-list">
            <RouterLink
              v-for="item in data.recentContent"
              :key="item.id"
              class="recent-row"
              :to="{ name: 'studio', query: { id: item.id } }"
            >
              <div class="recent-index">{{ String(data.recentContent.indexOf(item) + 1).padStart(2, '0') }}</div>
              <div class="list-main">
                <div class="list-title">{{ item.title }}</div>
                <div class="list-meta">更新于 {{ item.updatedAt.slice(0, 16).replace('T', ' ') }}</div>
              </div>
              <StatusTag :status="item.status" />
              <span class="row-arrow">↗</span>
            </RouterLink>
          </div>
        </section>
      </div>

      <section class="card ai-card">
        <div class="ai-copy">
          <span class="eyebrow">AI WORKSPACE</span>
          <h2 class="card-title">AI 服务</h2>
          <p class="page-sub">已配置 {{ fmt(data.aiStatus.modelConnections) }} 个模型连接。{{ data.aiStatus.note }}</p>
        </div>
        <div class="ai-status"><span class="status-dot" /> {{ fmt(data.aiStatus.modelConnections) }} 个模型连接</div>
        <RouterLink class="btn btn-ghost btn-sm" :to="{ name: 'settings' }">配置模型 <span>→</span></RouterLink>
      </section>
    </template>
  </div>
</template>

<style scoped>
.overview-page {
  gap: var(--space-6);
}
.overview-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-5);
  padding-bottom: var(--space-2);
}
.eyebrow {
  display: block;
  margin-bottom: var(--space-2);
  color: var(--color-accent);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.12em;
}
.overview-head .page-title {
  font-size: clamp(26px, 3vw, 34px);
  letter-spacing: -0.8px;
}
.overview-head .page-sub {
  margin-top: var(--space-2);
  font-size: var(--font-body);
}
.overview-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.overview-skeleton {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
}
.overview-skeleton .skeleton:last-child {
  grid-column: 1 / -1;
}
.overview-metrics {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
}
.metric-link {
  color: inherit;
  text-decoration: none;
  transition: border-color 140ms ease, box-shadow 140ms ease, transform 140ms ease;
}
.metric-link:hover {
  border-color: var(--color-border-strong);
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}
.metric-featured {
  border-color: color-mix(in srgb, var(--color-accent) 35%, var(--color-border));
}
.metric-alert .metric-value,
.metric-alert .metric-arrow {
  color: var(--color-danger);
}
.metric-value small {
  font-size: var(--font-section);
  font-weight: 500;
  color: var(--color-text-muted);
}
.metric-track {
  display: block;
  height: 5px;
  margin: var(--space-2) 0 var(--space-1);
  overflow: hidden;
  border-radius: 999px;
  background: var(--color-accent-soft);
}
.metric-track span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--color-accent);
  transition: width 300ms ease;
}
.metric-arrow,
.row-arrow {
  color: var(--color-accent);
  opacity: 0;
  transition: opacity 140ms ease, transform 140ms ease;
}
.metric-link:hover .metric-arrow,
.recent-row:hover .row-arrow {
  opacity: 1;
}
.overview-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr);
  gap: var(--space-4);
}
.overview-card {
  min-height: 300px;
  padding: var(--space-6);
}
.section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}
.section-heading .eyebrow {
  margin-bottom: var(--space-1);
}
.card-title {
  margin: 0;
  font-size: var(--font-section);
}
.text-link {
  color: var(--color-text-secondary);
  font-size: var(--font-sm);
  white-space: nowrap;
}
.text-link:hover {
  color: var(--color-accent);
}
.platform-list,
.recent-list {
  display: flex;
  flex-direction: column;
}
.platform-row,
.recent-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
  padding: var(--space-3) 0;
  border-top: 1px solid var(--color-border);
}
.platform-mark {
  display: grid;
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  place-items: center;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
  color: var(--color-accent);
  font-size: var(--font-sm);
  font-weight: 700;
}
.recent-row {
  color: inherit;
  text-decoration: none;
  transition: background 140ms ease;
}
.recent-row:hover {
  background: var(--color-surface-raised);
}
.recent-index {
  width: 24px;
  color: var(--color-text-muted);
  font-size: var(--font-xs);
  font-variant-numeric: tabular-nums;
}
.row-arrow {
  font-size: var(--font-section);
}
.compact-empty {
  min-height: 190px;
  align-items: flex-start;
  justify-content: center;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-5);
}
.empty-icon {
  display: grid;
  width: 30px;
  height: 30px;
  place-items: center;
  border-radius: 50%;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-size: var(--font-section);
}
.ai-card {
  display: flex;
  align-items: center;
  gap: var(--space-5);
  padding: var(--space-5) var(--space-6);
}
.ai-copy {
  min-width: 0;
  flex: 1;
}
.ai-copy .eyebrow {
  margin-bottom: var(--space-1);
}
.ai-copy .page-sub {
  margin: var(--space-2) 0 0;
}
.ai-status {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-success);
  font-size: var(--font-sm);
  white-space: nowrap;
}
.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-success);
}
@media (max-width: 1023px) {
  .overview-metrics,
  .overview-skeleton {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .overview-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 767px) {
  .overview-head {
    align-items: flex-start;
    flex-direction: column;
  }
  .overview-actions {
    width: 100%;
  }
  .overview-actions .btn {
    flex: 1;
  }
  .overview-metrics,
  .overview-skeleton {
    grid-template-columns: minmax(0, 1fr);
  }
  .overview-card,
  .ai-card {
    padding: var(--space-4);
  }
  .ai-card {
    align-items: flex-start;
    flex-direction: column;
    gap: var(--space-3);
  }
  .ai-card .btn {
    width: 100%;
  }
}
</style>
