<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import MetricCard from '../components/MetricCard.vue'
import StatusTag from '../components/StatusTag.vue'

const data = ref(null)
const loading = ref(true)
const error = ref('')

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
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">创作总览</h1>
        <p class="page-sub">今天该写什么、该发什么、哪里卡住了</p>
      </div>
      <button class="btn btn-ghost btn-sm" :disabled="loading" @click="load">刷新</button>
    </div>

    <div v-if="error" class="banner banner-danger" role="alert">
      <span>加载失败：{{ error }}</span>
      <button class="btn btn-ghost btn-sm" @click="load">重试</button>
    </div>

    <div v-if="loading" class="metric-grid">
      <div v-for="i in 5" :key="i" class="skeleton" style="height: 88px" />
    </div>

    <template v-else-if="data">
      <div class="metric-grid">
        <MetricCard
          label="本周发布进度"
          :value="`${data.publishProgress.published} / ${data.publishProgress.planned}`"
          :hint="data.publishProgress.planned === 0 ? '本周还没有排期' : `完成率 ${Math.round((data.publishProgress.ratio || 0) * 100)}%`"
        />
        <MetricCard label="待审核内容" :value="data.pendingReview" hint="发布前需要人工确认" />
        <MetricCard label="发布失败 / 需核实" :value="data.failedJobs" hint="不会自动重发，需先核实" />
        <MetricCard label="待同步账号" :value="data.unsyncedAccounts" hint="绑定但尚未成功同步" />
      </div>

      <div class="grid-12">
        <section class="card" style="grid-column: span 7">
          <h2 class="card-title">平台连接状态</h2>
          <div v-if="!data.platformStatus.length" class="empty">
            <span class="empty-title">还没有绑定平台账号</span>
            <span>绑定后可以在这里看到授权状态与自动发布开关。</span>
            <RouterLink class="btn btn-sm" :to="{ name: 'publishing' }">去绑定账号</RouterLink>
          </div>
          <div v-else class="list">
            <div v-for="item in data.platformStatus" :key="item.platform" class="list-row">
              <div class="list-main">
                <div class="list-title">{{ item.label }}</div>
                <div class="list-meta">
                  自动发布：{{ item.autoPublishEnabled ? '已开启' : '未开启' }} ·
                  最近同步：{{ item.lastSyncedAt ? item.lastSyncedAt.slice(0, 16).replace('T', ' ') : '尚未同步' }}
                </div>
              </div>
              <StatusTag :status="item.status" />
            </div>
          </div>
        </section>

        <section class="card" style="grid-column: span 5">
          <h2 class="card-title">最近稿件</h2>
          <div v-if="!data.recentContent.length" class="empty">
            <span class="empty-title">还没有草稿</span>
            <span>从一个想法开始，直接让 AI 生成初稿。</span>
            <RouterLink class="btn btn-sm" :to="{ name: 'studio' }">新建稿件</RouterLink>
          </div>
          <div v-else class="list">
            <RouterLink
              v-for="item in data.recentContent"
              :key="item.id"
              class="list-row"
              :to="{ name: 'studio', query: { id: item.id } }"
              style="color: inherit"
            >
              <div class="list-main">
                <div class="list-title">{{ item.title }}</div>
                <div class="list-meta">{{ item.updatedAt.slice(0, 16).replace('T', ' ') }}</div>
              </div>
              <StatusTag :status="item.status" />
            </RouterLink>
          </div>
        </section>

        <section class="card" style="grid-column: span 12">
          <div class="row">
            <h2 class="card-title" style="margin: 0">AI 服务</h2>
            <div class="spacer" />
            <RouterLink class="btn btn-ghost btn-sm" :to="{ name: 'settings' }">配置模型</RouterLink>
          </div>
          <p class="page-sub" style="margin-top: var(--space-2)">
            已配置 {{ fmt(data.aiStatus.modelConnections) }} 个模型连接。
            {{ data.aiStatus.note }}
          </p>
        </section>
      </div>
    </template>
  </div>
</template>
