<script setup>
/**
 * WechatDraftPanel — 公众号专属草稿配置与排版面板
 *
 * Props:
 *   open        Boolean — 是否打开
 *   title       String  — 稿件标题
 *   body        String  — 稿件正文（Markdown）
 *   contentId   String  — 稿件 ID，用于推送后刷新
 *   coverAssetId    String — 默认封面素材（自动配图产出）
 *   inlineAssetIds  Array  — 默认正文图片素材，按正文顺序
 *
 * Emits:
 *   close       — 关闭抽屉
 *   published   — 成功推送到草稿箱后
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NAlert, NButton, NInput, NSelect } from 'naive-ui'
import MarkdownEditor from '../../components/MarkdownEditor.vue'
import { apiUrl, fetchBlobUrl } from '../../api/client'
import { useWechatPublish } from '../../composables/useWechatPublish'
import { useLayoutComponents } from '../../composables/useLayoutComponents'
import { usePublishFlow } from './flow/usePublishFlow.js'
import { toast } from '../../ui/feedback'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
  body: { type: String, default: '' },
  contentId: { type: String, default: '' },
  // 自动配图产出的默认素材：封面 1 张 + 正文插图（按编号顺序）
  coverAssetId: { type: String, default: '' },
  inlineAssetIds: { type: Array, default: () => [] },
})
const emit = defineEmits(['close', 'published'])

const {
  wechat,
  wechatError,
  wechatForm,
  wechatAccountId,
  wechatPushError,
  wechatPreview,
  pushAccountOptions,
  allThemes,
  wechatThemeId,
  layoutBusy,
  layoutHtml,
  layoutError,
  assets,
  imageAssets,
  inlineOptions,
  loadWechat,
  loadThemes,
  loadAssets,
  scheduleLayout,
  insertSnippet,
  previewWechatDraft,
  pushWechatDraft,
  initForm,
} = useWechatPublish()

// 区块按钮来自后端小组件注册表（与「微信排版 → 组件库」同一份），不要求每篇都用。
const { components: BLOCK_COMPONENTS, load: loadBlockComponents } = useLayoutComponents()

function syncDraftAdapter(account, form) {
  wechatAccountId.value = account.id
  wechatForm.title = form.title
  wechatForm.body = form.body
  wechatForm.coverId = form.coverAssetId || form.assetIds[0] || ''
  wechatForm.inlineIds = form.assetIds.slice(1)
  wechatThemeId.value = form.themeId || wechatThemeId.value
}

const draftFlow = usePublishFlow({
  lockedMode: 'draft',
  lockStep1: true,
  draftAdapter: {
    async preview(account, form) {
      syncDraftAdapter(account, form)
      await previewWechatDraft()
      return {
        issues: (wechatPreview.value?.issues || []).map((issue) => ({
          level: issue.level || 'error',
          message: issue.message || String(issue),
        })),
        canPush: wechatPreview.value?.canPush,
      }
    },
    async submit(account, form) {
      syncDraftAdapter(account, form)
      return pushWechatDraft()
    },
  },
})

const pushing = ref(false)
const assetPicker = ref(null) // 'cover' | 'inline' | null
const assetPickerFilter = ref('all')

const pickedInlineAssets = computed(() =>
  wechatForm.inlineIds.map((id) => assetById(id)).filter(Boolean),
)
const availableInlineAssets = computed(() =>
  imageAssets.value.filter((asset) => !wechatForm.inlineIds.includes(asset.id)),
)
const pickerAssets = computed(() => {
  const source = assetPicker.value === 'inline' ? availableInlineAssets.value : imageAssets.value
  if (assetPickerFilter.value === 'ai') return source.filter((asset) => asset.sourceType === 'generated')
  if (assetPickerFilter.value === 'upload') return source.filter((asset) => asset.sourceType !== 'generated')
  return source
})

function openAssetPicker(type) {
  assetPicker.value = type
  assetPickerFilter.value = 'all'
}

function closeAssetPicker() {
  assetPicker.value = null
}

// 正文里的图片语法 → 公众号 {{imageN}} 占位
const ASSET_IMAGE_RE = /!\[([^\]]*)\]\(([^)\s]*\/assets\/([^/)"'\s]+)\/raw[^)]*)\)/g

function assetById(id) {
  return assets.value.find((asset) => asset.id === id) || null
}

function assetThumb(asset) {
  if (!asset) return ''
  return asset.url ? apiUrl(asset.url) : ''
}

/**
 * 把正文中已插入的图片转成 {{imageN}} 占位符，并把素材按正文顺序选进发布表单。
 * 正文没有图片时，用自动配图的结果作为默认正文图片。
 */
function preparePublishImages() {
  // 素材列表只取最近 100 张；老配图不在列表里也照样交给后端解析
  wechatForm.coverId = props.coverAssetId || ''
  const ids = []
  const nextBody = String(wechatForm.body || '').replace(
    ASSET_IMAGE_RE,
    (_whole, alt, _url, assetId) => {
      if (!ids.includes(assetId)) ids.push(assetId)
      const n = ids.indexOf(assetId) + 1
      const caption = String(alt || '').replace(/[{}|]/g, '').trim()
      return `{{image${n}${caption ? `|${caption}` : ''}}}`
    },
  )
  wechatForm.body = nextBody
  const fallback = Array.isArray(props.inlineAssetIds) ? [...props.inlineAssetIds] : []
  wechatForm.inlineIds = ids.length ? ids : fallback
}

function selectCover(id) {
  wechatForm.coverId = id
  closeAssetPicker()
  scheduleLayout()
}

function selectAssetFromPicker(id) {
  if (assetPicker.value === 'cover') {
    selectCover(id)
    return
  }
  if (!wechatForm.inlineIds.includes(id)) {
    wechatForm.inlineIds.push(id)
    scheduleLayout()
  }
  closeAssetPicker()
}

function toggleInline(id) {
  const index = wechatForm.inlineIds.indexOf(id)
  if (index >= 0) wechatForm.inlineIds.splice(index, 1)
  else wechatForm.inlineIds.push(id)
  scheduleLayout()
}

function moveInline(index, offset) {
  const target = index + offset
  if (target < 0 || target >= wechatForm.inlineIds.length) return
  const list = wechatForm.inlineIds
  ;[list[index], list[target]] = [list[target], list[index]]
  scheduleLayout()
}

function removeInline(index) {
  wechatForm.inlineIds.splice(index, 1)
  scheduleLayout()
}

// 预览里的 asset:// 占位换成可访问的本地图，避免正文配图显示为裂图
const previewHtml = ref('')
let previewObjectUrls = []
let previewToken = 0

async function hydratePreview() {
  const token = ++previewToken
  previewObjectUrls.forEach((url) => URL.revokeObjectURL(url))
  previewObjectUrls = []
  const html = layoutHtml.value
  if (!html) {
    previewHtml.value = ''
    return
  }
  const ids = [...new Set([...html.matchAll(/asset:\/\/([\w-]+)/g)].map((m) => m[1]))]
  let replaced = html
  for (const id of ids) {
    try {
      const url = await fetchBlobUrl(`/assets/${id}/raw`)
      if (token !== previewToken) {
        URL.revokeObjectURL(url)
        return
      }
      previewObjectUrls.push(url)
      replaced = replaced.split(`asset://${id}`).join(url)
    } catch {
      // 单张素材取图失败不影响其余预览
    }
  }
  if (token === previewToken) previewHtml.value = replaced
}

watch(layoutHtml, hydratePreview)

onBeforeUnmount(() => {
  previewObjectUrls.forEach((url) => URL.revokeObjectURL(url))
  previewObjectUrls = []
})

const hasCover = computed(() => Boolean(wechatForm.coverId))
const accountSelectOptions = computed(() =>
  pushAccountOptions.value.map((opt) => ({ label: opt.label, value: opt.id })),
)

// 抽屉打开时初始化（每次打开都刷新素材，刚生成的配图才能立刻选到）
watch(() => props.open, async (val) => {
  if (!val) return
  loadBlockComponents().catch(() => { /* 区块按钮缺失不阻塞发布 */ })
  await Promise.all([loadWechat(), loadThemes(), loadAssets()])
  // 每次打开都用最新的 title/body 初始化表单
  initForm({ title: props.title, body: props.body, contentId: props.contentId })
  preparePublishImages()
  scheduleLayout()
})

async function handlePush() {
  if (!hasCover.value) {
    toast('请先选择封面图，公众号草稿必须有封面', 'warning')
    return
  }
  pushing.value = true
  try {
    await draftFlow.load()
    draftFlow.reset({
      contentId: props.contentId,
      title: wechatForm.title,
      body: wechatForm.body,
      mode: 'draft',
      accountIds: wechatAccountId.value ? [wechatAccountId.value] : [],
      assetIds: [wechatForm.coverId, ...wechatForm.inlineIds].filter(Boolean),
      themeId: wechatThemeId.value,
      coverAssetId: wechatForm.coverId,
      inlineAssetIds: wechatForm.inlineIds,
    })
    await draftFlow.submit()
    const job = draftFlow.results.value.find((result) => result.ok)?.job
    if (job && !draftFlow.results.value.some((result) => !result.ok)) {
      toast('草稿已推送到公众号草稿箱，请到后台预览并群发。')
      emit('published')
    } else if (draftFlow.results.value.some((result) => !result.ok)) {
      toast(draftFlow.results.value.find((result) => !result.ok)?.error || '草稿推送失败', 'error')
    }
  } finally {
    pushing.value = false
  }
}

function handleClose() {
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="publish-drawer-overlay" @click.self="handleClose">
      <div class="publish-drawer" role="dialog" aria-label="发布到公众号">
        <!-- 顶部栏 -->
        <header class="publish-drawer-header">
          <span class="publish-drawer-title">发布到公众号</span>
          <span class="spacer" />
          <button class="pub-close-btn" @click="handleClose">✕</button>
        </header>

        <div class="publish-drawer-body">
          <!-- 左：文章预览 -->
          <section class="publish-preview-pane">
            <div class="publish-pane-head">
              <span class="publish-pane-label">内容预览</span>
              <span class="publish-pane-sub">来自最新保存版本</span>
            </div>
            <div v-if="layoutHtml" class="publish-layout-preview" v-html="previewHtml || layoutHtml" />
            <div v-else-if="layoutBusy" class="publish-preview-loading">排版渲染中…</div>
            <div v-else-if="layoutError" class="publish-preview-error">排版失败：{{ layoutError }}</div>
            <div v-else class="publish-preview-fallback">
              <MarkdownEditor
                :model-value="body"
                :readonly="true"
                :toolbar="{}"
                class="publish-preview-md"
              />
            </div>
          </section>

          <!-- 右：发布配置 -->
          <aside class="publish-config-pane">
            <div class="publish-pane-head">
              <span class="publish-pane-label">发布配置</span>
            </div>

            <n-alert v-if="wechatError" type="error" :show-icon="false">{{ wechatError }}</n-alert>
            <n-alert v-if="wechatPushError" type="error" :show-icon="false">{{ wechatPushError }}</n-alert>

            <div v-if="!wechat" class="pub-loading">加载公众号配置中…</div>
            <template v-else>
              <n-alert
                v-if="!wechat.configured || !wechat.account"
                type="warning"
                :show-icon="false"
              >
                {{ !wechat.configured
                  ? '未配置公众号凭据。请前往「发布管理」绑定账号。'
                  : '尚未绑定公众号账号。请前往「发布管理」绑定账号。'
                }}
              </n-alert>

              <!-- 目标公众号 -->
              <div class="pub-field">
                <label class="pub-label" for="pub-account">目标公众号</label>
                <n-select
                  id="pub-account"
                  v-model:value="wechatAccountId"
                  :options="accountSelectOptions"
                  :placeholder="pushAccountOptions.length ? '选择公众号' : '暂无已连接账号'"
                  :disabled="!pushAccountOptions.length"
                />
              </div>

              <!-- 标题 -->
              <div class="pub-field">
                <label class="pub-label" for="pub-title">标题</label>
                <n-input
                  id="pub-title"
                  v-model:value="wechatForm.title"
                  :maxlength="64"
                  show-count
                  placeholder="文章标题"
                />
              </div>

              <!-- 摘要 -->
              <div class="pub-field">
                <label class="pub-label" for="pub-digest">摘要（可选）</label>
                <n-input
                  id="pub-digest"
                  v-model:value="wechatForm.digest"
                  type="textarea"
                  :maxlength="120"
                  show-count
                  :autosize="{ minRows: 3, maxRows: 6 }"
                  placeholder="不填则自动截取正文前 120 字"
                />
              </div>

              <!-- 作者 -->
              <div class="pub-field">
                <label class="pub-label" for="pub-author">作者（可选）</label>
                <n-input id="pub-author" v-model:value="wechatForm.author" placeholder="作者名" />
              </div>

              <!-- 排版主题 -->
              <div class="pub-field">
                <label class="pub-label">排版主题</label>
                <div class="pub-theme-list">
                  <button
                    v-for="theme in allThemes"
                    :key="theme.id"
                    class="pub-theme-btn"
                    :class="{ 'is-active': wechatThemeId === theme.id }"
                    @click="wechatThemeId = theme.id; scheduleLayout()"
                  >{{ theme.name }}</button>
                </div>
              </div>

              <!-- 区块插入：追加到当前这次推送的正文，不写回工作室稿件 -->
              <div class="pub-field">
                <label class="pub-label">插入区块</label>
                <div class="pub-theme-list">
                  <button
                    v-for="block in BLOCK_COMPONENTS"
                    :key="block.id"
                    type="button"
                    class="pub-theme-btn"
                    @click="insertSnippet(block.snippet)"
                  >{{ block.label }}</button>
                </div>
              </div>

              <!-- 图片配置：封面与正文图分离，默认只展示已选内容 -->
              <section class="pub-image-config" aria-labelledby="pub-image-config-title">
                <div class="pub-image-config-head">
                  <div>
                    <h2 id="pub-image-config-title" class="pub-section-title">图片配置</h2>
                    <p class="pub-section-hint">封面 1 张，正文图按文章中的位置排列</p>
                  </div>
                  <span class="pub-image-summary">{{ wechatForm.inlineIds.length }} 张正文图</span>
                </div>

                <div class="pub-cover-card">
                  <div class="pub-image-card-head">
                    <div>
                      <strong>封面图</strong>
                      <span>公众号文章必须选择 1 张</span>
                    </div>
                    <span v-if="wechatForm.coverId" class="pub-status-chip">已选择</span>
                    <span v-else class="pub-status-chip is-warning">待选择</span>
                  </div>
                  <div v-if="wechatForm.coverId" class="pub-cover-selected">
                    <img :src="assetThumb(assetById(wechatForm.coverId))" alt="当前封面" />
                    <div class="pub-cover-meta">
                      <strong>{{ assetById(wechatForm.coverId)?.originalName || '当前封面' }}</strong>
                      <span>{{ assetById(wechatForm.coverId)?.sourceType === 'generated' ? 'AI 自动生成' : '素材库图片' }}</span>
                    </div>
                    <button type="button" class="pub-secondary-btn" @click="openAssetPicker('cover')">更换封面</button>
                  </div>
                  <div v-else class="pub-cover-empty">
                    <span>还没有选择封面图</span>
                    <button type="button" class="pub-primary-soft-btn" @click="openAssetPicker('cover')">选择封面</button>
                  </div>
                </div>

                <div class="pub-inline-card">
                  <div class="pub-image-card-head">
                    <div>
                      <strong>正文图片</strong>
                      <span>已匹配的图片会自动对应正文位置</span>
                    </div>
                    <button type="button" class="pub-secondary-btn" @click="openAssetPicker('inline')">+ 添加正文图</button>
                  </div>
                  <div v-if="pickedInlineAssets.length" class="pub-inline-list">
                    <div v-for="(asset, index) in pickedInlineAssets" :key="asset.id" class="pub-inline-row">
                      <span class="pub-inline-no">{{ index + 1 }}</span>
                      <img class="pub-inline-thumb" :src="assetThumb(asset)" alt="" />
                      <div class="pub-inline-meta">
                        <strong>{{ asset.originalName }}</strong>
                        <span>{{ asset.sourceType === 'generated' ? 'AI 自动生成 · 对应正文位置' : '素材库图片' }}</span>
                      </div>
                      <div class="pub-inline-actions">
                        <button type="button" class="pub-mini-btn" :disabled="index === 0" @click="moveInline(index, -1)">上移</button>
                        <button type="button" class="pub-mini-btn" :disabled="index === pickedInlineAssets.length - 1" @click="moveInline(index, 1)">下移</button>
                        <button type="button" class="pub-mini-btn pub-mini-btn-danger" @click="removeInline(index)">移除</button>
                      </div>
                    </div>
                  </div>
                  <div v-else class="pub-inline-empty">
                    <span>正文中暂无已选图片</span>
                    <button type="button" class="pub-secondary-btn" @click="openAssetPicker('inline')">从素材库添加</button>
                  </div>
                </div>
              </section>

              <!-- 底部推送按钮 -->
              <div class="pub-actions">
                <n-button
                  type="primary"
                  size="small"
                  :disabled="pushing || !wechat.configured || !hasCover"
                  :loading="pushing"
                  @click="handlePush"
                >
                  {{ pushing ? '推送中…' : '推送到草稿箱' }}
                </n-button>
                <n-button size="small" quaternary @click="handleClose">取消</n-button>
              </div>
              <p v-if="wechat.configured && !hasCover" class="pub-hint pub-hint-left">
                请选择一张封面图后再推送
              </p>
            </template>

            <!-- 独立素材选择器：不再把全部素材常驻在封面/正文区域 -->
            <div v-if="assetPicker" class="pub-picker-backdrop" @click.self="closeAssetPicker">
              <section class="pub-picker" role="dialog" aria-modal="true" :aria-label="assetPicker === 'cover' ? '选择封面图' : '添加正文图片'">
                <header class="pub-picker-head">
                  <div>
                    <strong>{{ assetPicker === 'cover' ? '选择封面图' : '添加正文图片' }}</strong>
                    <span>{{ assetPicker === 'cover' ? '只能选择 1 张' : '选择后会加入正文图片列表' }}</span>
                  </div>
                  <button type="button" class="pub-close-btn" @click="closeAssetPicker">×</button>
                </header>
                <div class="pub-picker-filters" role="tablist" aria-label="素材来源">
                  <button type="button" :class="{ 'is-active': assetPickerFilter === 'all' }" @click="assetPickerFilter = 'all'">全部素材</button>
                  <button type="button" :class="{ 'is-active': assetPickerFilter === 'ai' }" @click="assetPickerFilter = 'ai'">本篇自动生成</button>
                  <button type="button" :class="{ 'is-active': assetPickerFilter === 'upload' }" @click="assetPickerFilter = 'upload'">手动上传</button>
                </div>
                <div v-if="pickerAssets.length" class="pub-picker-grid">
                  <button v-for="asset in pickerAssets" :key="asset.id" type="button" class="pub-picker-item" @click="selectAssetFromPicker(asset.id)">
                    <img :src="assetThumb(asset)" :alt="asset.originalName" loading="lazy" />
                    <span class="pub-picker-item-meta">
                      <strong>{{ asset.originalName }}</strong>
                      <small>{{ asset.sourceType === 'generated' ? 'AI 自动生成' : '手动上传' }}</small>
                    </span>
                    <span v-if="(assetPicker === 'cover' && wechatForm.coverId === asset.id) || (assetPicker === 'inline' && wechatForm.inlineIds.includes(asset.id))" class="pub-picker-check">已选</span>
                  </button>
                </div>
                <div v-else class="pub-picker-empty">暂无符合条件的图片素材</div>
              </section>
            </div>
          </aside>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.publish-drawer-overlay {
  position: fixed;
  inset: 0;
  background: var(--color-overlay);
  z-index: 200;
  display: flex;
  align-items: stretch;
  justify-content: flex-end;
}

.publish-drawer {
  width: min(960px, 100vw);
  height: 100vh;
  background: var(--color-surface);
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-lg);
}

.publish-drawer-header {
  display: flex;
  align-items: center;
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.publish-drawer-title {
  font-size: var(--font-body);
  font-weight: 700;
  color: var(--color-text-primary);
}

.spacer { flex: 1; }

.pub-close-btn {
  background: none;
  border: none;
  font-size: 16px;
  color: var(--color-text-secondary);
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 4px;
}

.pub-close-btn:hover { background: var(--color-surface-raised); }

.publish-drawer-body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 1fr 380px;
}

.publish-preview-pane,
.publish-config-pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow-y: auto;
}

.publish-preview-pane {
  border-right: 1px solid var(--color-border);
}

.publish-pane-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-5);
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.publish-pane-label {
  font-size: var(--font-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}

.publish-pane-sub {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.publish-layout-preview {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-5) var(--space-6);
  font-size: 15px;
  line-height: 1.85;
}

.publish-preview-loading,
.publish-preview-error {
  padding: var(--space-6);
  color: var(--color-text-muted);
  font-size: var(--font-sm);
}

.publish-preview-fallback {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.publish-preview-md {
  min-height: 100%;
}

.publish-config-pane {
  padding: var(--space-4) var(--space-5);
  gap: var(--space-3);
}

.pub-loading {
  color: var(--color-text-muted);
  font-size: var(--font-sm);
  padding: var(--space-4) 0;
}

.pub-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.pub-label {
  font-size: var(--font-xs);
  font-weight: 600;
  color: var(--color-text-secondary);
}

.pub-image-config {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.pub-image-config-head,
.pub-image-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.pub-section-title {
  margin: 0;
  font-size: 13px;
  color: var(--color-text-primary);
}

.pub-section-hint,
.pub-image-card-head span,
.pub-cover-meta span,
.pub-inline-meta span {
  display: block;
  margin: 3px 0 0;
  color: var(--color-text-muted);
  font-size: 11px;
  line-height: 1.4;
}

.pub-image-summary {
  padding: 3px 7px;
  border-radius: 10px;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-size: 11px;
}

.pub-cover-card,
.pub-inline-card {
  padding: 10px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-surface-raised);
}

.pub-status-chip {
  padding: 2px 7px;
  border-radius: 10px;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-size: 10px;
}

.pub-status-chip.is-warning {
  background: color-mix(in srgb, var(--color-warning) 14%, transparent);
  color: var(--color-warning);
}

.pub-cover-selected {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 9px;
}

.pub-cover-selected img {
  width: 58px;
  height: 44px;
  object-fit: cover;
  border-radius: 5px;
}

.pub-cover-meta,
.pub-inline-meta {
  min-width: 0;
  flex: 1;
}

.pub-cover-meta strong,
.pub-inline-meta strong {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-primary);
  font-size: 11px;
}

.pub-cover-empty,
.pub-inline-empty {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 9px;
  padding: 10px;
  border: 1px dashed var(--color-border);
  border-radius: 6px;
  color: var(--color-text-muted);
  font-size: 11px;
}

.pub-primary-soft-btn,
.pub-secondary-btn {
  flex-shrink: 0;
  padding: 5px 8px;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: var(--color-surface);
  color: var(--color-text-secondary);
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}

.pub-primary-soft-btn {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
  color: var(--color-accent);
}

.pub-primary-soft-btn:hover,
.pub-secondary-btn:hover {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.pub-inline-meta {
  overflow: hidden;
}

.pub-mini-btn-danger:hover:not(:disabled) {
  border-color: var(--color-danger);
  color: var(--color-danger);
}

.pub-picker-backdrop {
  position: fixed;
  inset: 0;
  z-index: 400;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: var(--color-overlay);
}

.pub-picker {
  width: min(520px, 100%);
  max-height: min(620px, 90vh);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-surface);
  box-shadow: var(--shadow-lg);
}

.pub-picker-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid var(--color-border);
}

.pub-picker-head strong,
.pub-picker-head span {
  display: block;
}

.pub-picker-head strong {
  color: var(--color-text-primary);
  font-size: 14px;
}

.pub-picker-head span {
  margin-top: 3px;
  color: var(--color-text-muted);
  font-size: 11px;
}

.pub-picker-filters {
  display: flex;
  gap: 4px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--color-border);
}

.pub-picker-filters button {
  padding: 5px 9px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--color-text-muted);
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}

.pub-picker-filters button.is-active {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}

.pub-picker-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  overflow-y: auto;
  padding: 12px;
}

.pub-picker-item {
  position: relative;
  overflow: hidden;
  padding: 0;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-surface);
  text-align: left;
  cursor: pointer;
}

.pub-picker-item:hover {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 1px var(--color-accent);
}

.pub-picker-item > img {
  display: block;
  width: 100%;
  aspect-ratio: 1.2;
  object-fit: cover;
}

.pub-picker-item-meta {
  display: block;
  padding: 6px 7px;
}

.pub-picker-item-meta strong,
.pub-picker-item-meta small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pub-picker-item-meta strong {
  color: var(--color-text-primary);
  font-size: 11px;
}

.pub-picker-item-meta small {
  margin-top: 2px;
  color: var(--color-text-muted);
  font-size: 10px;
}

.pub-picker-check {
  position: absolute;
  top: 6px;
  right: 6px;
  padding: 2px 5px;
  border-radius: 9px;
  background: var(--color-accent);
  color: var(--color-on-accent);
  font-size: 10px;
}

.pub-picker-empty {
  padding: 36px 16px;
  color: var(--color-text-muted);
  font-size: 12px;
  text-align: center;
}

.pub-hint {
  font-size: 11px;
  color: var(--color-text-muted);
  text-align: right;
}

.pub-hint-left {
  text-align: left;
}

.pub-label-extra {
  margin-left: 6px;
  font-weight: 400;
  font-size: 11px;
  color: var(--color-text-muted);
}

/* 素材缩略图：紧凑四列，列表内部滚动，避免把推送按钮挤出屏幕 */
.pub-asset-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
  max-height: 132px;
  overflow-y: auto;
  padding: 2px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-raised);
}

.pub-asset-grid--inline {
  max-height: 108px;
  margin-top: 6px;
}

.pub-asset {
  position: relative;
  display: block;
  width: 100%;
  aspect-ratio: 1 / 1;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 4px;
  overflow: hidden;
  background: var(--color-surface);
  cursor: pointer;
}

.pub-asset img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.pub-asset:hover {
  border-color: var(--color-accent);
}

.pub-asset.is-active {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 1px var(--color-accent);
}

.pub-asset-badge {
  position: absolute;
  top: 3px;
  right: 3px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: var(--color-accent);
  color: var(--color-on-accent);
  font-size: 10px;
  line-height: 16px;
  text-align: center;
}

/* 已选正文图：顺序、缩略图、调整按钮 */
.pub-inline-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 150px;
  overflow-y: auto;
}

.pub-inline-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 6px;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-surface);
}

.pub-inline-no {
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-size: 10px;
  line-height: 16px;
  text-align: center;
}

.pub-inline-thumb {
  width: 24px;
  height: 24px;
  border-radius: 3px;
  object-fit: cover;
  flex-shrink: 0;
}

.pub-inline-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: var(--color-text-secondary);
}

.pub-inline-actions {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}

.pub-mini-btn {
  padding: 2px 5px;
  font-size: 10px;
  font-family: inherit;
  border: 1px solid var(--color-border);
  border-radius: 3px;
  background: var(--color-surface);
  color: var(--color-text-secondary);
  cursor: pointer;
}

.pub-mini-btn:hover:not(:disabled) {
  border-color: var(--color-accent);
  color: var(--color-text-primary);
}

.pub-mini-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}


.pub-theme-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.pub-theme-btn {
  padding: 4px 10px;
  font-size: var(--font-xs);
  font-weight: 500;
  font-family: inherit;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text-secondary);
  cursor: pointer;
}

.pub-theme-btn.is-active {
  background: var(--color-text-primary);
  border-color: var(--color-text-primary);
  color: var(--color-surface);
}

.pub-actions {
  display: flex;
  gap: var(--space-2);
  margin-top: auto;
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border);
}
</style>
