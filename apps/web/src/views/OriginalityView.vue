<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NEmpty,
  NGrid,
  NGridItem,
  NIcon,
  NLayout,
  NLayoutContent,
  NList,
  NListItem,
  NProgress,
  NRadioGroup,
  NSelect,
  NSpace,
  NStatistic,
  NTag,
  useMessage,
} from 'naive-ui'
import { CheckCircleOutlined as SuccessIcon, CloseCircleOutlined as ErrorIcon } from '@vicons/antd'
import { api, downloadAuthenticated } from '../api/client'

const message = useMessage()

const drafts = ref([])
const selectedId = ref(null)
const compareScope = ref('all')
const loading = ref(false)
const exportLoading = ref(false)
const error = ref('')
const analysis = ref(null)
const history = ref([])

const levelStyles = {
  excellent: { color: '#18a058', backgroundColor: '#f0f9ff' },
  good: { color: '#0969da', backgroundColor: '#f0f9ff' },
  fair: { color: '#f59e0b', backgroundColor: '#fffbf0' },
  poor: { color: '#d32f2f', backgroundColor: '#fef2f2' },
}

const levelLabels = { excellent: '优秀', good: '良好', fair: '中等', poor: '偏低' }

const draftOptions = computed(() =>
  drafts.value.map((draft) => ({
    label: draft.versionNo ? `${draft.title}（v${draft.versionNo}）` : draft.title,
    value: draft.id,
    disabled: !draft.currentVersionId,
  }))
)

const currentVersionId = computed(
  () => drafts.value.find((draft) => draft.id === selectedId.value)?.currentVersionId || null
)

const currentLevelStyle = computed(() => {
  if (!analysis.value) return {}
  return levelStyles[analysis.value.level] || {}
})

const currentLevelLabel = computed(() => levelLabels[analysis.value?.level] || '未知')

// 相似度是「越高越像」，进度条填得越满越危险，所以按阈值换色而不是固定色
const similarityColor = computed(() => {
  const value = analysis.value?.scoreboard?.similarity ?? 0
  if (value >= 60) return '#d32f2f'
  if (value >= 30) return '#f59e0b'
  return '#18a058'
})

const segments = computed(() => {
  const articles = analysis.value?.details?.similarArticles || []
  return articles.flatMap((article) =>
    (article.matchingSnippets || []).map((snippet) => ({ ...snippet, articleTitle: article.title }))
  )
})

async function loadDrafts() {
  try {
    drafts.value = (await api.get('/content?fields=list&limit=200')) || []
    // 默认选中最近更新的那篇，省得每次都要点
    if (!selectedId.value && drafts.value.length) selectedId.value = drafts.value[0].id
  } catch (err) {
    message.error(err.message || '稿件列表加载失败')
  }
}

async function handleAnalyze() {
  if (!currentVersionId.value) {
    message.warning('请选择一篇已保存版本的稿件')
    return
  }

  loading.value = true
  error.value = ''
  try {
    analysis.value = await api.post('/content/analyze-originality', {
      contentVersionId: currentVersionId.value,
      compareScope: compareScope.value,
      useCache: true,
    })
    await loadHistory()
  } catch (err) {
    error.value = err.message || '检测失败'
    message.error(error.value)
  } finally {
    loading.value = false
  }
}

async function loadHistory() {
  if (!currentVersionId.value) return
  try {
    const response = await api.get(`/originality/${currentVersionId.value}/history?limit=10`)
    history.value = response.items || []
  } catch (err) {
    console.error('加载检测历史失败:', err)
  }
}

async function handleExport(format) {
  const analysisId = analysis.value?.analysisId
  if (!analysisId) {
    message.warning('请先完成检测')
    return
  }
  const endpoint = { txt: 'report/txt', markdown: 'report/markdown', png: 'report/png' }[format]
  if (!endpoint) return
  exportLoading.value = true
  try {
    await downloadAuthenticated(`/originality/${analysisId}/${endpoint}`, `原创度报告.${format}`)
    message.success('报告已开始下载')
  } catch (err) {
    message.error(err.message || '导出失败')
  } finally {
    exportLoading.value = false
  }
}

function resetAnalysis() {
  analysis.value = null
  error.value = ''
  history.value = []
}

function formatDateTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

onMounted(loadDrafts)
</script>

<template>
  <NLayout class="originality-view">
    <NLayoutContent content-style="padding: 20px;">
      <div class="originality-container">
        <!-- 标题 -->
        <div class="page-header">
          <h1>文章原创度检测</h1>
          <p>一键检测文章原创度，获得多维度评分与改进建议</p>
        </div>

        <!-- 输入区域 -->
        <NCard class="input-section" :segmented="{ content: 'hard', footer: 'soft' }" size="large">
          <template #header>
            <span>选择要检测的稿件</span>
          </template>

          <div class="settings-grid">
            <div class="setting-item">
              <label>稿件</label>
              <NSelect
                v-model:value="selectedId"
                :options="draftOptions"
                filterable
                placeholder="选择一篇已保存版本的稿件"
                size="large"
              />
              <p class="setting-hint">
                检测对象是稿件的当前版本；正文改过之后再检测会新增一条记录。
              </p>
            </div>

            <div class="setting-item">
              <label>比对范围</label>
              <NRadioGroup
                v-model:value="compareScope"
                :options="[
                  { label: '全部稿件', value: 'all' },
                  { label: '仅我的稿件', value: 'own' },
                ]"
                size="small"
              />
              <p class="setting-hint">与库内其它稿件比对，同一篇的历史版本不计入。</p>
            </div>
          </div>

          <template #footer>
            <NSpace :size="12" justify="space-between">
              <NButton text type="primary" @click="resetAnalysis"> 清空结果 </NButton>
              <NButton type="primary" :loading="loading" size="large" @click="handleAnalyze">
                开始检测
              </NButton>
            </NSpace>
          </template>
        </NCard>

        <!-- 错误提示 -->
        <NAlert v-if="error" type="error" style="margin-top: 20px;">
          {{ error }}
        </NAlert>

        <!-- 结果展示 -->
        <div v-if="analysis" class="results-section" style="margin-top: 20px;">
          <!-- 结论 -->
          <NAlert
            :type="analysis.level === 'poor' ? 'warning' : 'success'"
            :bordered="false"
            style="margin-bottom: 20px;"
            title="检测结论"
          >
            {{ analysis.summary }}
          </NAlert>

          <!-- 总分卡片 -->
          <NGrid cols="1 m:2 l:4" responsive="screen" :x-gap="20" :y-gap="20" style="margin-bottom: 20px;">
            <!-- 综合评分 -->
            <NGridItem>
              <NCard hoverable style="height: 100%;">
                <div class="score-card" :style="currentLevelStyle">
                  <div class="score-number">{{ analysis.totalScore }}</div>
                  <div class="score-label">
                    {{
                      {
                        excellent: '优秀',
                        good: '良好',
                        fair: '中等',
                        poor: '偏低',
                      }[analysis.level] || '未知'
                    }}
                  </div>
                </div>
              </NCard>
            </NGridItem>

            <!-- 检测耗时 -->
            <NGridItem>
              <NCard hoverable>
                <NStatistic
                  label="检测耗时"
                  :value="`${analysis.processingTimeMs}ms`"
                  style="font-size: 16px;"
                />
              </NCard>
            </NGridItem>

            <!-- 比对语料 -->
            <NGridItem>
              <NCard hoverable>
                <NStatistic
                  label="比对语料"
                  :value="analysis.corpusSize"
                  suffix="篇"
                  style="font-size: 16px;"
                />
              </NCard>
            </NGridItem>

            <!-- 数据量 -->
            <NGridItem>
              <NCard hoverable>
                <NStatistic
                  label="相似文章"
                  :value="analysis.details?.similarArticles?.length || 0"
                  style="font-size: 16px;"
                />
              </NCard>
            </NGridItem>
          </NGrid>

          <!-- 五维度评分 -->
          <NCard title="多维度评分" style="margin-bottom: 20px;">
            <NGrid cols="1 m:2 l:5" responsive="screen" :x-gap="16" :y-gap="16">
              <NGridItem>
                <div class="score-item">
                  <div class="score-item-label">相似度</div>
                  <template v-if="analysis.corpusSize">
                    <NProgress
                      :percentage="analysis.scoreboard.similarity"
                      :color="similarityColor"
                      show-indicator
                    />
                    <div class="score-item-value">{{ analysis.scoreboard.similarity }}/100</div>
                    <div class="score-item-hint">越低越好 · 权重 40%</div>
                  </template>
                  <template v-else>
                    <div class="score-item-value">无从判断</div>
                    <div class="score-item-hint">库内暂无可比对稿件</div>
                  </template>
                </div>
              </NGridItem>
              <NGridItem>
                <div class="score-item">
                  <div class="score-item-label">观点独特性</div>
                  <NProgress :percentage="analysis.scoreboard.originality" show-indicator color="#18a058" />
                  <div class="score-item-value">{{ analysis.scoreboard.originality }}/100</div>
                  <div class="score-item-hint">越高越好 · 权重 25%</div>
                </div>
              </NGridItem>
              <NGridItem>
                <div class="score-item">
                  <div class="score-item-label">结构层次</div>
                  <NProgress :percentage="analysis.scoreboard.structure" show-indicator color="#0969da" />
                  <div class="score-item-value">{{ analysis.scoreboard.structure }}/100</div>
                  <div class="score-item-hint">越高越好 · 权重 15%</div>
                </div>
              </NGridItem>
              <NGridItem>
                <div class="score-item">
                  <div class="score-item-label">数据支撑度</div>
                  <NProgress :percentage="analysis.scoreboard.dataAccuracy" show-indicator color="#f59e0b" />
                  <div class="score-item-value">{{ analysis.scoreboard.dataAccuracy }}/100</div>
                  <div class="score-item-hint">越高越好 · 权重 12%</div>
                </div>
              </NGridItem>
              <NGridItem>
                <div class="score-item">
                  <div class="score-item-label">文风辨识度</div>
                  <NProgress :percentage="analysis.scoreboard.styleUniqueness" show-indicator color="#d946ef" />
                  <div class="score-item-value">{{ analysis.scoreboard.styleUniqueness }}/100</div>
                  <div class="score-item-hint">越高越好 · 权重 8%</div>
                </div>
              </NGridItem>
            </NGrid>
          </NCard>

          <!-- 相似文章 -->
          <NCard v-if="analysis.details?.similarArticles?.length" title="相似文章" style="margin-bottom: 20px;">
            <NList :bordered="false" clickable>
              <NListItem v-for="(article, idx) in analysis.details.similarArticles" :key="idx">
                <template #prefix>
                  <NIcon size="20" style="margin-right: 12px;">
                    <ErrorIcon />
                  </NIcon>
                </template>
                <div class="similar-article-item">
                  <div class="similar-article-title">{{ article.title }}</div>
                  <div class="similar-article-meta">
                    <NTag type="warning"> 相似度: {{ (article.similarity * 100).toFixed(1) }}% </NTag>
                    <span v-if="article.matchingSnippets?.length" class="matching-count">
                      匹配段落: {{ article.matchingSnippets.length }}
                    </span>
                  </div>
                  <div v-if="article.matchingSnippets?.length" class="matching-snippets">
                    <div v-for="(snippet, sidx) in article.matchingSnippets.slice(0, 2)" :key="sidx" class="snippet">
                      {{ snippet.targetText || snippet.sourceText }}
                    </div>
                  </div>
                </div>
              </NListItem>
            </NList>
          </NCard>

          <!-- 风险标记 -->
          <NCard v-if="analysis.details?.riskFlags?.length" title="风险提示" type="warning" style="margin-bottom: 20px;">
            <NList>
              <NListItem v-for="(flag, idx) in analysis.details.riskFlags" :key="idx">
                <template #prefix>
                  <NIcon size="20">
                    <ErrorIcon />
                  </NIcon>
                </template>
                <div>
                  <strong>{{ flag.position }}</strong>
                  <div style="font-size: 13px; color: #666;">{{ flag.reason }}</div>
                </div>
                <template #suffix>
                  <NTag :type="flag.severity === 'error' ? 'error' : 'warning'">
                    {{ flag.severity === 'error' ? '严重' : '提示' }}
                  </NTag>
                </template>
              </NListItem>
            </NList>
          </NCard>

          <!-- 重合片段 -->
          <NCard v-if="segments.length" title="重合片段" style="margin-bottom: 20px;">
            <div v-for="(segment, idx) in segments" :key="idx" class="segment-row">
              <div class="segment-row-head">
                <span>{{ segment.position }}</span>
                <NTag size="small" type="warning">重合 {{ Math.round(segment.similarity * 100) }}%</NTag>
              </div>
              <p class="segment-row-line">本文：{{ segment.targetText }}</p>
              <p class="segment-row-line segment-row-muted">
                对方：{{ segment.sourceText }}
                <span v-if="segment.matchedTitle">（《{{ segment.matchedTitle }}》）</span>
              </p>
            </div>
          </NCard>

          <!-- 关键观点 -->
          <NCard v-if="analysis.details?.keyPoints?.length" title="关键观点" style="margin-bottom: 20px;">
            <NSpace :size="8">
              <NTag
                v-for="point in analysis.details.keyPoints"
                :key="point.point"
                :type="point.sources ? 'default' : 'success'"
                round
              >
                {{ point.point }}
                <span style="opacity: 0.7; margin-left: 4px;">
                  {{ point.sources ? `库内 ${point.sources} 篇提过` : '未出现过' }}
                </span>
              </NTag>
            </NSpace>
          </NCard>

          <!-- 改进建议 -->
          <NCard v-if="analysis.recommendations?.length" title="改进建议" style="margin-bottom: 20px;">
            <NList :bordered="false">
              <NListItem v-for="(rec, idx) in analysis.recommendations" :key="idx">
                <template #prefix>
                  <NIcon size="20" style="margin-right: 12px;">
                    <SuccessIcon />
                  </NIcon>
                </template>
                {{ rec }}
              </NListItem>
            </NList>
          </NCard>

          <!-- 检测历史 -->
          <NCard v-if="history.length" title="检测历史" style="margin-bottom: 20px;">
            <NList>
              <NListItem v-for="item in history" :key="item.analysisId">
                <div>
                  <strong>{{ item.totalScore }}</strong>
                  <span style="margin-left: 8px; color: #666;">
                    {{ levelLabels[item.level] || item.level }} · 相似度
                    {{ item.corpusSize ? item.similarity + '%' : '样本不足' }}
                  </span>
                </div>
                <template #suffix>
                  <span style="font-size: 12px; color: #999;">{{ formatDateTime(item.analyzedAt) }}</span>
                </template>
              </NListItem>
            </NList>
          </NCard>

          <!-- 导出选项 -->
          <NCard title="导出报告">
            <NSpace>
              <NButton
                type="primary"
                :loading="exportLoading"
                @click="handleExport('txt')"
              >
                导出 TXT
              </NButton>
              <NButton
                type="primary"
                secondary
                :loading="exportLoading"
                @click="handleExport('markdown')"
              >
                导出 Markdown
              </NButton>
              <NButton
                type="primary"
                secondary
                :loading="exportLoading"
                @click="handleExport('png')"
              >
                导出图片
              </NButton>
            </NSpace>
          </NCard>
        </div>

        <!-- 空态 -->
        <NEmpty v-else-if="!loading" description="尚未进行检测" style="margin-top: 40px;" />
      </div>
    </NLayoutContent>
  </NLayout>
</template>

<style scoped>
.originality-view {
  width: 100%;
  height: 100%;
}

.originality-container {
  max-width: 1400px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 24px;
}

.page-header h1 {
  margin: 0;
  font-size: 28px;
  font-weight: 600;
  margin-bottom: 8px;
}

.page-header p {
  margin: 0;
  font-size: 14px;
  color: #666;
}

.input-section {
  margin-bottom: 20px;
}

.settings-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 20px;
  margin-bottom: 16px;
}

.setting-item label {
  display: block;
  margin-bottom: 8px;
  font-weight: 500;
  font-size: 14px;
}

.setting-hint {
  margin: 8px 0 0 0;
  font-size: 12px;
  color: #999;
}

.results-section {
  animation: slideIn 0.3s ease-out;
}

@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.score-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 24px;
  border-radius: 8px;
  text-align: center;
  min-height: 140px;
}

.score-number {
  font-size: 48px;
  font-weight: 700;
  line-height: 1;
  margin-bottom: 8px;
}

.score-label {
  font-size: 14px;
  font-weight: 500;
}

.score-item {
  padding: 12px;
  border-radius: 6px;
  background: #f5f5f5;
}

.score-item-label {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}

.score-item-value {
  font-size: 16px;
  font-weight: 600;
  margin-top: 8px;
}

.score-item-hint {
  font-size: 12px;
  color: #999;
  margin-top: 4px;
}

.similar-article-item {
  flex: 1;
}

.similar-article-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 8px;
}

.similar-article-meta {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 8px;
}

.matching-count {
  font-size: 12px;
  color: #666;
}

.matching-snippets {
  margin-top: 8px;
}

.snippet {
  padding: 8px;
  margin: 4px 0;
  background: #fef2f2;
  border-left: 2px solid #d32f2f;
  border-radius: 2px;
  font-size: 12px;
  color: #666;
}

/* 重合片段 */
.segment-row {
  padding: 10px 12px;
  margin-bottom: 8px;
  background: #fafafa;
  border-radius: 4px;
}

.segment-row-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  font-size: 12px;
  color: #666;
}

.segment-row-line {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: #333;
}

.segment-row-muted {
  color: #888;
}
</style>
