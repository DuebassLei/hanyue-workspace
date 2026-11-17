<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NAlert, NButton, NEmpty, NFormItem, NInput, NModal, NSelect, NSpace } from 'naive-ui'
import { useRouter } from 'vue-router'
import { api, fetchBlobUrl } from '../api/client'
import { useSessionStore } from '../stores/session'

const PAGE_SIZE = 24
const router = useRouter()
const session = useSessionStore()
// 用会话令牌是否就绪作为加载门槛：刷新时 store 先挂载，此组件可能早于令牌可用
const authed = computed(() => session.ready && !session.needsLogin && Boolean(session.token))

const meta = ref(null)
const groups = ref([])
const styles = ref([])
const total = ref(0)
const pages = ref(0)
const page = ref(1)
const keyword = ref('')
const activeGroup = ref('')
const loading = ref(true)
const error = ref('')

const colors = ref([])
const colorId = ref('')
const accentColorId = ref(null)
const theme = ref('')
const composing = ref(false)
const composed = ref(null)
const pendingStyle = ref(null)
const copied = ref('')

// 风格提示词详情弹层
const viewingStyle = ref(null)
const detailLoading = ref(false)

const showDetail = computed({
  get: () => viewingStyle.value !== null,
  set: (open) => {
    if (!open) viewingStyle.value = null
  },
})

// 弹层内展示的合成结果只认当前查看的风格
const detailComposed = computed(
  () =>
    Boolean(composed.value) &&
    viewingStyle.value !== null &&
    composed.value.style.number === viewingStyle.value.number,
)

const colorGroups = computed(() => {
  const map = new Map()
  for (const item of colors.value) {
    const key = item.categoryZh || item.category || '其他'
    if (!map.has(key)) map.set(key, [])
    map.get(key).push(item)
  }
  return [...map.entries()].map(([label, items]) => ({ label, items }))
})

const activeColor = computed(() => colors.value.find((item) => item.id === colorId.value) || null)
const activeAccent = computed(
  () => colors.value.find((item) => item.id === accentColorId.value) || null,
)

const accentOptions = computed(() => [
  { label: '不指定', value: null },
  ...colors.value.map((item) => ({ label: `${item.id} · ${item.nameZh}`, value: item.id })),
])

// 参考图与色卡接口需要 Bearer 令牌，<img src> 直连会 401，
// 这里统一按需拉取二进制并缓存 object URL。
// 用 ref 包裹 Map，避免模板里读到的是「未响应」的旧值导致一直显示加载中
const blobUrls = ref(new Map())

function releaseBlobs(prefix) {
  const store = blobUrls.value
  for (const [key, url] of store) {
    if (prefix && !key.startsWith(prefix)) continue
    URL.revokeObjectURL(url)
    store.delete(key)
  }
  blobUrls.value = new Map(store)
}

function cachedBlob(key) {
  return blobUrls.value.get(key) || ''
}

async function loadBlob(key, path) {
  const cached = blobUrls.value.get(key)
  if (cached) return cached
  const url = await fetchBlobUrl(path)
  const store = new Map(blobUrls.value)
  const existing = store.get(key)
  if (existing) URL.revokeObjectURL(existing)
  store.set(key, url)
  blobUrls.value = store
  return url
}

async function loadStyleThumbs() {
  await Promise.all(
    styles.value
      .filter((item) => item.referenceAvailable)
      .map((item) =>
        loadBlob(`style:${item.number}`, `/handdraw/styles/${item.number}/reference`).catch(() => {}),
      ),
  )
}

let searchTimer = null

async function loadStyles() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({ page: String(page.value), pageSize: String(PAGE_SIZE) })
    if (keyword.value.trim()) params.set('q', keyword.value.trim())
    if (activeGroup.value) params.set('group', activeGroup.value)
    const data = await api.get(`/handdraw/styles?${params.toString()}`)
    styles.value = data.items || []
    total.value = data.total || 0
    pages.value = data.pages || 0
    if ((data.groups || []).length) groups.value = data.groups
    releaseBlobs('style:')
    loadStyleThumbs()
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function onSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    loadStyles()
  }, 260)
}

function pickGroup(key) {
  activeGroup.value = activeGroup.value === key ? '' : key
  page.value = 1
  loadStyles()
}

function goPage(delta) {
  const next = page.value + delta
  if (next < 1 || next > pages.value) return
  page.value = next
  loadStyles()
}

function pickStyle(item) {
  pendingStyle.value = item
  if (theme.value.trim()) compose(item)
}

function goPrompts() {
  router.push({ name: 'prompts' })
}

function goImages() {
  const style = pendingStyle.value
  if (!style) {
    router.push({ name: 'images' })
    return
  }
  const query = { handdrawStyle: style.number }
  if (theme.value.trim()) query.theme = theme.value.trim()
  if (colorId.value) query.color = colorId.value
  if (accentColorId.value) query.accent = accentColorId.value
  router.push({ name: 'images', query })
}

async function compose(item) {
  if (!theme.value.trim()) {
    error.value = '先在右侧填写画面主题，再选择风格合成提示词'
    return
  }
  composing.value = true
  error.value = ''
  copied.value = ''
  try {
    const result = await api.post('/handdraw/compose', {
      styleNumber: item.number,
      theme: theme.value.trim(),
      colorId: colorId.value || null,
      accentColorId: accentColorId.value || null,
    })
    // 弹层里合成的结果同样回报到侧栏，便于带着参数去生图
    pendingStyle.value = item
    composed.value = result
  } catch (err) {
    error.value = err.message
  } finally {
    composing.value = false
  }
}

async function openDetail(item) {
  viewingStyle.value = item
  detailLoading.value = true
  try {
    const detail = await api.get(`/handdraw/styles/${item.number}`)
    viewingStyle.value = { ...item, ...detail }
  } catch (err) {
    error.value = err.message
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  viewingStyle.value = null
}

function viewFromSide() {
  if (pendingStyle.value) openDetail(pendingStyle.value)
}

async function copyText(text, key) {
  if (!text) return
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(text)
    copied.value = key
  } catch {
    error.value = '复制失败，请手动选中提示词复制'
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [metaData, colorList] = await Promise.all([
      api.get('/handdraw/meta'),
      api.get('/handdraw/colors'),
    ])
    meta.value = metaData
    colors.value = colorList
    for (const item of colors.value) {
      if (!item.swatchUrl) continue
      loadBlob(`swatch:${item.id}`, item.swatchUrl).catch(() => {})
    }
  } catch (err) {
    error.value = err.message
  }
  await loadStyles()
}

// 会话就绪（含令牌）后再加载；刷新时本组件可能早于令牌可用
watch(authed, (ready) => ready && load(), { immediate: true })
onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
  releaseBlobs()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">提示词库</h1>
        <p class="page-sub">
          从手绘风格库挑选画风与主题色，自动合成可直接出图的提示词。
        </p>
      </div>
      <n-button secondary @click="goImages">去图片生成</n-button>
    </div>

    <n-space class="prompt-tabs" role="tablist" aria-label="提示词库视图" :size="8">
      <n-button secondary size="small" role="tab" aria-selected="false" @click="goPrompts">
        我的提示词
      </n-button>
      <n-button type="primary" size="small" role="tab" aria-selected="true" disabled>
        手绘风格
      </n-button>
    </n-space>

    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>

    <div class="handdraw-layout">
      <section class="card handdraw-main">
        <div class="row handdraw-toolbar">
          <n-input
            v-model:value="keyword"
            class="handdraw-search"
            clearable
            placeholder="搜索风格名称、作者或编号"
            aria-label="搜索手绘风格"
            @update:value="onSearch"
          />
          <div class="spacer" />
          <span class="field-hint">{{ total }} 种风格</span>
        </div>

        <div class="row row-tight handdraw-groups">
          <button
            class="tag"
            :class="{ 'tag-accent': !activeGroup }"
            @click="pickGroup('')"
          >
            全部
          </button>
          <button
            v-for="item in groups"
            :key="item.key"
            class="tag"
            :class="{ 'tag-accent': activeGroup === item.key }"
            @click="pickGroup(item.key)"
          >
            {{ item.key }} · {{ item.count }}
          </button>
        </div>

        <div v-if="loading && !styles.length" class="skeleton" style="height: 240px; margin-top: var(--space-3)" />
        <div v-else-if="!styles.length" class="empty">
          <n-empty description="换个关键词或切换分组再试试。" />
        </div>
        <div v-else class="style-grid" :class="{ 'is-busy': loading }">
          <div
            v-for="item in styles"
            :key="item.number"
            class="style-card"
            role="button"
            tabindex="0"
            :class="{ 'is-active': pendingStyle?.number === item.number }"
            @click="pickStyle(item)"
            @keydown.enter="pickStyle(item)"
          >
            <span class="style-thumb">
              <img
                v-if="cachedBlob(`style:${item.number}`)"
                :src="cachedBlob(`style:${item.number}`)"
                :alt="item.generationName"
              />
              <span v-else class="style-thumb-empty">{{ item.referenceAvailable ? '参考图加载中' : '无参考图' }}</span>
            </span>
            <span class="style-name" :title="item.generationName">{{ item.generationName }}</span>
            <span class="style-meta">{{ item.number }}<template v-if="item.legacyNumber"> · {{ item.legacyNumber }}</template></span>
            <span class="style-meta">{{ item.referenceAuthor }}</span>
            <span class="style-actions">
              <n-button quaternary size="small" block @click.stop="openDetail(item)">查看提示词</n-button>
            </span>
          </div>
        </div>

        <div v-if="pages > 1" class="row handdraw-pager">
          <n-button secondary size="small" :disabled="page <= 1" @click="goPage(-1)">上一页</n-button>
          <span class="field-hint">{{ page }} / {{ pages }}</span>
          <n-button secondary size="small" :disabled="page >= pages" @click="goPage(1)">
            下一页
          </n-button>
        </div>
      </section>

      <aside class="card handdraw-side">
        <n-form-item label="画面主题" label-placement="top" :show-feedback="false">
          <n-input
            v-model:value="theme"
            type="textarea"
            :autosize="{ minRows: 3 }"
            placeholder="例如：春日庭院里晒太阳的橘猫"
          />
        </n-form-item>

        <div class="field">
          <span class="field-label">主题色</span>
          <div v-for="group in colorGroups" :key="group.label" class="color-group">
            <span class="color-group-label">{{ group.label }}</span>
            <div class="row row-tight color-swatches">
              <button
                v-for="item in group.items"
                :key="item.id"
                class="color-dot"
                :class="{ 'is-active': colorId === item.id }"
                :title="`${item.nameZh}（${item.nameEn}）`"
                @click="colorId = colorId === item.id ? '' : item.id"
              >
                <img
                  v-if="cachedBlob(`swatch:${item.id}`)"
                  :src="cachedBlob(`swatch:${item.id}`)"
                  :alt="item.nameZh"
                />
                <span v-else class="color-dot-fallback" />
              </button>
            </div>
          </div>
          <span class="field-hint">
            {{ activeColor ? `主色：${activeColor.nameZh}` : '未选主色（不输出主题色句）' }}
          </span>
        </div>

        <n-form-item label="点缀色（可选）" label-placement="top" :show-feedback="false">
          <n-select
            v-model:value="accentColorId"
            :options="accentOptions"
            placeholder="不指定"
            clearable
          />
          <span v-if="activeAccent" class="field-hint">点缀：{{ activeAccent.nameZh }}</span>
        </n-form-item>

        <div class="row">
          <n-button v-if="pendingStyle" quaternary size="small" @click="viewFromSide">
            查看提示词
          </n-button>
          <div class="spacer" />
          <n-button
            type="primary"
            size="small"
            :loading="composing"
            :disabled="composing || !theme.trim() || !pendingStyle"
            @click="pendingStyle && compose(pendingStyle)"
          >
            {{ pendingStyle ? '合成提示词' : '先选一种风格' }}
          </n-button>
        </div>

        <div v-if="composed" class="compose-result">
          <div class="compose-head">
            <span class="compose-title">{{ composed.style.generationName }}</span>
            <span class="tag">{{ composed.modelCode }}</span>
          </div>
          <div class="row row-tight">
            <span class="tag">{{ composed.activationSource }}</span>
            <span v-if="composed.needReference" class="tag tag-accent">需参考图</span>
            <span v-if="!composed.referenceAvailable" class="tag">参考图缺失</span>
          </div>
          <div class="field">
            <div class="row">
              <span class="field-label">中文提示词</span>
              <div class="spacer" />
              <n-button quaternary size="small" @click="copyText(composed.promptZh, 'zh')">
                {{ copied === 'zh' ? '已复制' : '复制' }}
              </n-button>
            </div>
            <p class="compose-prompt">{{ composed.promptZh }}</p>
          </div>
          <div class="field">
            <div class="row">
              <span class="field-label">English prompt</span>
              <div class="spacer" />
              <n-button quaternary size="small" @click="copyText(composed.promptEn, 'en')">
                {{ copied === 'en' ? '已复制' : '复制' }}
              </n-button>
            </div>
            <p class="compose-prompt">{{ composed.promptEn }}</p>
          </div>
        </div>

        <p v-if="meta" class="attribution">
          风格库来自
          <a :href="meta.attribution.repository" target="_blank" rel="noreferrer">
            {{ meta.attribution.repository.replace('https://', '') }}
          </a>
          ，作者 {{ meta.attribution.author }}，{{ meta.attribution.license }}。
        </p>
      </aside>
    </div>

    <n-modal
      v-model:show="showDetail"
      preset="card"
      :title="viewingStyle?.generationName || '风格提示词详情'"
      style="width: min(760px, calc(100vw - 32px))"
      :mask-closable="!composing"
    >
      <template v-if="viewingStyle">
        <div class="row detail-head">
          <span class="field-hint">
            {{ viewingStyle.number
            }}<template v-if="viewingStyle.legacyNumber"> · {{ viewingStyle.legacyNumber }}</template>
            · {{ viewingStyle.referenceAuthor }}
          </span>
        </div>

        <div class="detail-body">
          <div class="detail-ref">
            <img
              v-if="cachedBlob(`style:${viewingStyle.number}`)"
              :src="cachedBlob(`style:${viewingStyle.number}`)"
              :alt="viewingStyle.generationName"
            />
            <span v-else class="style-thumb-empty">
              {{ viewingStyle.referenceAvailable ? '参考图加载中' : '该风格没有参考图' }}
            </span>
          </div>

          <div class="detail-info">
            <div class="row row-tight">
              <span class="tag">{{ viewingStyle.groupKey }} · {{ viewingStyle.groupName }}</span>
              <span v-if="viewingStyle.activationSource" class="tag tag-accent">
                {{ viewingStyle.activationSource }}
              </span>
              <span v-if="viewingStyle.needReference" class="tag">需参考图</span>
            </div>
            <p v-if="detailLoading" class="field-hint">正在读取风格特征…</p>
            <div v-else class="field">
              <span class="field-label">风格特征（会写入提示词）</span>
              <p class="compose-prompt">{{ viewingStyle.traits || '该模型下不附加风格特征，仅用风格名称激活' }}</p>
            </div>
            <span class="field-hint">
              提示词内不出现风格编号；需要参考图的风格会在出图时自动附加本张参考图。
            </span>
          </div>
        </div>

        <div class="field">
          <div class="row">
            <span class="field-label">画面主题</span>
            <div class="spacer" />
            <n-button
              type="primary"
              size="small"
              :loading="composing"
              :disabled="composing || !theme.trim()"
              @click="compose(viewingStyle)"
            >
              {{ detailComposed ? '重新合成' : '合成提示词' }}
            </n-button>
          </div>
          <n-input
            v-model:value="theme"
            type="textarea"
            :autosize="{ minRows: 3 }"
            placeholder="填写画面主题后即可看到完整提示词"
          />
        </div>

        <div v-if="detailComposed" class="compose-result">
          <div class="field">
            <div class="row">
              <span class="field-label">中文提示词</span>
              <div class="spacer" />
              <n-button quaternary size="small" @click="copyText(composed.promptZh, 'zh')">
                {{ copied === 'zh' ? '已复制' : '复制' }}
              </n-button>
            </div>
            <p class="compose-prompt">{{ composed.promptZh }}</p>
          </div>
          <div class="field">
            <div class="row">
              <span class="field-label">English prompt</span>
              <div class="spacer" />
              <n-button quaternary size="small" @click="copyText(composed.promptEn, 'en')">
                {{ copied === 'en' ? '已复制' : '复制' }}
              </n-button>
            </div>
            <p class="compose-prompt">{{ composed.promptEn }}</p>
          </div>
          <div class="row">
            <div class="spacer" />
            <n-button type="primary" size="small" @click="goImages">用这个风格去生图</n-button>
          </div>
        </div>
        <p v-else class="field-hint">填写主题并合成后，可在这里查看完整中英文提示词。</p>
      </template>
      <template #footer>
        <n-space justify="end">
          <n-button quaternary @click="closeDetail">关闭</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.prompt-tabs {
  margin-bottom: var(--space-3);
  width: fit-content;
}
.handdraw-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: var(--space-3);
  align-items: start;
}
.handdraw-search {
  flex: 1 1 220px;
  min-width: 0;
  max-width: 360px;
}
.handdraw-groups {
  flex-wrap: wrap;
  margin-top: var(--space-2);
}
.handdraw-groups .tag {
  cursor: pointer;
  background: transparent;
}
.handdraw-groups .tag-accent {
  background: var(--color-accent-soft);
}
.style-grid.is-busy {
  opacity: 0.6;
  pointer-events: none;
}
.style-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(148px, 1fr));
  gap: var(--space-2);
  margin-top: var(--space-3);
}
.style-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  padding: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
  text-align: left;
  cursor: pointer;
}
.style-card.is-active {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 1px var(--color-accent);
}
.style-thumb {
  display: flex;
  align-items: center;
  justify-content: center;
  aspect-ratio: 1;
  overflow: hidden;
  border-radius: var(--radius-sm);
  background: var(--color-surface-muted, var(--color-surface));
}
.style-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.style-thumb-empty {
  font-size: var(--font-xs, 12px);
  color: var(--color-text-tertiary, var(--color-text-secondary));
}
.style-name {
  font-size: var(--font-sm);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.style-meta {
  font-size: var(--font-xs, 12px);
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.style-actions {
  margin-top: 2px;
}
.detail-head {
  margin-bottom: var(--space-2);
}
.detail-body {
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr);
  gap: var(--space-3);
  align-items: start;
  margin-bottom: var(--space-3);
}
.detail-ref {
  display: flex;
  align-items: center;
  justify-content: center;
  aspect-ratio: 1;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-muted, var(--color-surface));
}
.detail-ref img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.detail-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}
@media (max-width: 767px) {
  .detail-body {
    grid-template-columns: minmax(0, 1fr);
  }
  .detail-ref {
    max-width: 220px;
  }
}
.handdraw-pager {
  justify-content: center;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-3);
}
.handdraw-side {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  position: sticky;
  top: var(--space-3);
}
.color-group {
  margin-top: var(--space-2);
}
.color-group-label {
  font-size: var(--font-xs, 12px);
  color: var(--color-text-secondary);
}
.color-swatches {
  flex-wrap: wrap;
  margin-top: 4px;
}
.color-dot {
  width: 28px;
  height: 28px;
  padding: 0;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: 50%;
  background: none;
  cursor: pointer;
}
.color-dot.is-active {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 2px var(--color-accent);
}
.color-dot img,
.color-dot-fallback {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.color-dot-fallback {
  background: var(--color-surface-muted, var(--color-surface));
}
.compose-result {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px dashed var(--color-border);
}
.compose-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.compose-title {
  flex: 1;
  font-weight: 600;
}
.compose-prompt {
  margin: 4px 0 0;
  max-height: 180px;
  overflow: auto;
  padding: var(--space-2);
  border-radius: var(--radius-sm);
  background: var(--color-surface-muted);
  font-size: var(--font-sm);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.attribution {
  margin: 0;
  font-size: var(--font-xs, 12px);
  color: var(--color-text-secondary);
}
.attribution a {
  color: var(--color-accent);
}
@media (max-width: 1023px) {
  .handdraw-layout {
    grid-template-columns: minmax(0, 1fr);
  }
  .handdraw-side {
    position: static;
  }
}
@media (max-width: 767px) {
  .handdraw-search {
    flex: 1 1 100%;
    max-width: none;
  }
}
</style>
