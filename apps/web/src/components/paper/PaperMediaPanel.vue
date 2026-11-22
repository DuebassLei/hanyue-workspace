<script setup>
/**
 * 成稿台右栏「封面 · 插图」。
 *
 * 受控组件：所有写操作都回整篇快照（emit('saved', article)），由父级替换 state；
 * 自己不拉 article、不跳路由。
 *
 * 后端封面/插图都是「入队即返回」，所以 running 时必须每 2s 调一次 sync 拉结果；
 * 轮询统一收在一个定时器里，`onBeforeUnmount` 与稿件切换都会清掉，避免离开页面还在打接口。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { NButton, NInput, NModal, NSelect } from 'naive-ui'
import { api, apiUrl } from '../../api/client'
import { studioExtraApi } from '../../api/studioExtra'
import PaperSvgCardPicker from './PaperSvgCardPicker.vue'
import PaperAssetPicker from './PaperAssetPicker.vue'

const props = defineProps({
  article: { type: Object, default: null },
  selectedBlockId: { type: String, default: '' },
  // 正文里多选的素材段落（SVG 卡按这些段合并生成）
  sourceBlockIds: { type: Array, default: () => [] },
  busy: { type: String, default: '' },
})
const emit = defineEmits(['saved', 'notify', 'pick-in-paper', 'select-block'])

const ASPECTS = ['2.35:1', '16:9', '4:3', '1:1', '3:4', '9:16']
const capabilities = ref(null)
const capabilityError = ref('')
const aspectOptions = computed(() => ASPECTS.map((value) => ({ label: `${value}${capabilities.value?.ratios?.includes(value) ? '' : '（未确认）'}`, value, disabled: !capabilities.value?.ratios?.includes(value) })))
const styles = [
  { label: '不指定风格', value: '' },
  { label: '写实摄影', value: '写实摄影，真实光影，细节丰富' },
  { label: '水彩插画', value: '水彩插画，柔和色彩，纸张质感' },
  { label: '扁平插画', value: '扁平插画，简洁构图，清晰色块' },
  { label: '国风水墨', value: '国风水墨，留白构图，淡雅色调' },
]

const catalog = ref([])
const prompts = ref([])
const promptError = ref('')
const coverPrompt = ref('')
const coverStyle = ref('')
const coverRatio = ref('2.35:1')
const inlinePrompt = ref('')
const inlineStyle = ref('')
const inlineRatio = ref('16:9')
const previewImage = ref('')
const downloading = ref(false)
const action = ref('')
const error = ref('')
// AI 拟好的提示词候选：点一条就填进输入框，还能继续手改
const suggestions = ref([])
const suggestScope = ref('')
// 选图弹窗当前服务于哪一块：cover=封面，inline=正文插图
const assetPicker = ref('')

let timer = null
let disposed = false
let failures = 0

const busyAction = computed(() => Boolean(action.value) || Boolean(props.busy))
const isAction = (label) => action.value === label

const cover = computed(() => props.article?.cover || {})
/** cover 的 ready 判定要兼容两种历史结构：新契约写 status/assetId，老数据只有 id */
const coverStatus = computed(() => {
  const current = cover.value
  if (current.status) return current.status
  return current.assetId || current.id ? 'ready' : 'idle'
})
const coverImage = computed(() => (coverStatus.value === 'ready' && cover.value.url ? apiUrl(cover.value.url) : ''))
const coverError = computed(() => cover.value.error || '')

const blocks = computed(() => props.article?.blocks || [])
const inlineItems = computed(() => props.article?.inlineDiagrams || [])
const selectedBlock = computed(() => blocks.value.find((block) => block.id === props.selectedBlockId) || null)
const selectedExcerpt = computed(() => {
  const text = String(selectedBlock.value?.text || '').replace(/\s+/g, ' ').trim()
  return text.length > 42 ? `${text.slice(0, 42)}…` : text
})
const runningInline = computed(() => inlineItems.value.filter((item) => item.status === 'running').length)

const statusLabel = { running: '生成中', ready: '已就绪', failed: '失败', idle: '未生成' }

/**
 * 提示词库按类型分：封面用 cover、正文插图用 illustration。
 * 某一类为空时退回 normal，最后再退到全部——不让用户因为分类没配齐就用不了模板。
 */
function promptOptionsOf(type) {
  const list = prompts.value || []
  const scoped = list.filter((item) => item.promptType === type)
  const fallback = scoped.length ? scoped : list.filter((item) => item.promptType === 'normal')
  const rows = fallback.length ? fallback : list
  return rows.map((item) => ({
    label: `${item.title}${item.category ? ` · ${item.category}` : ''}`,
    value: item.id,
  }))
}

const coverPromptOptions = computed(() => promptOptionsOf('cover'))
const inlinePromptOptions = computed(() => promptOptionsOf('illustration'))

function applyPrompt(id, target) {
  const item = prompts.value.find((entry) => entry.id === id)
  if (!item) return
  if (target === 'cover') coverPrompt.value = item.content
  else inlinePrompt.value = item.content
}

function composedPrompt(prompt, style, fallback) {
  if (!style) return prompt.trim() || undefined
  return `${prompt.trim() || fallback}；风格：${style}`
}

async function downloadPreview() {
  if (!previewImage.value || downloading.value) return
  downloading.value = true
  try {
    const response = await fetch(previewImage.value)
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `成稿图片.${({ 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/png': 'png' })[blob.type] || 'png'}`
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (err) {
    error.value = `下载失败：${err.message}`
    emit('notify', error.value)
  } finally {
    downloading.value = false
  }
}

async function loadCapabilities() {
  try {
    capabilities.value = await api.get('/studio/image-capabilities')
    capabilityError.value = ''
  } catch (err) {
    capabilities.value = null
    capabilityError.value = `生图比例能力加载失败：${err.message}`
  }
}

async function loadPrompts() {
  try {
    prompts.value = await api.get('/prompts?enabled=true')
    promptError.value = ''
  } catch (err) {
    promptError.value = `提示词库加载失败：${err.message}`
  }
}

function blockIndex(blockId) {
  const index = blocks.value.findIndex((block) => block.id === blockId)
  return index >= 0 ? index + 1 : 0
}

function stopPolling() {
  if (timer) clearInterval(timer)
  timer = null
}

function hasRunning() {
  return coverStatus.value === 'running' || runningInline.value > 0
}

function ensurePolling() {
  if (disposed || !props.article?.id || !hasRunning()) return stopPolling()
  if (timer) return
  timer = setInterval(syncPending, 2000)
}

async function syncPending() {
  const articleId = props.article?.id
  if (!articleId) return stopPolling()
  try {
    if (coverStatus.value === 'running') {
      const snapshot = await studioExtraApi.syncCover(articleId)
      if (snapshot) emit('saved', snapshot)
    }
    if (runningInline.value > 0) {
      const snapshot = await studioExtraApi.syncInline(articleId)
      if (snapshot) emit('saved', snapshot)
    }
    failures = 0
  } catch (err) {
    // 单拍失败不断轮询：后台任务可能只是还没落库，下一拍再试。
    // 连错 10 拍（约 20s）就停表并给出可读原因，避免接口异常时一直空转。
    failures += 1
    if (failures >= 10) {
      stopPolling()
      error.value = err.message || '自动检查生成结果失败，请手动重试'
    }
  }
}

async function loadCatalog() {
  const articleId = props.article?.id
  if (!articleId) return
  try {
    const data = await studioExtraApi.renderOptions(articleId)
    if (disposed) return
    catalog.value = data?.catalog?.svgCards || []
  } catch {
    catalog.value = []
  }
}

async function run(actionName, work, message) {
  if (busyAction.value) return
  action.value = actionName
  error.value = ''
  failures = 0
  try {
    const snapshot = await work()
    if (snapshot) emit('saved', snapshot)
    if (message) emit('notify', message)
    await nextTick()
    ensurePolling()
  } catch (err) {
    error.value = err.message || '操作失败，请重试'
    emit('notify', error.value)
  } finally {
    action.value = ''
  }
}

const generateCover = () =>
  run(
    'cover',
    () =>
      studioExtraApi.generateCover(props.article.id, {
        prompt: composedPrompt(coverPrompt.value, coverStyle.value, `为文章《${props.article.title || props.article.topic || '文章'}》生成封面`),
        aspectRatio: coverRatio.value,
      }),
    '封面已提交生成，出图后自动刷新',
  )

const removeCover = () =>
  run('cover-remove', () => studioExtraApi.pickCover(props.article.id, null), '封面已移除')

/** 从素材图库挑一张当封面：不需要生图，直接换掉当前封面。 */
const pickCoverFromLibrary = (asset) =>
  run('cover-pick', () => studioExtraApi.pickCover(props.article.id, asset.id), '封面已换成素材图')

const generateInline = () =>
  run(
    'inline',
    () =>
      studioExtraApi.generateInline(props.article.id, {
        blockId: props.selectedBlockId,
        prompt: composedPrompt(inlinePrompt.value, inlineStyle.value, `为文章《${props.article.title || props.article.topic || '文章'}》的段落配图：${selectedBlock.value?.text || ''}`),
        aspectRatio: inlineRatio.value,
        position: 'after',
      }),
    '插图已提交生成，完成后自动插到该段之后',
  )

/** 从素材图库挑一张当插图：插到当前选中的那一段之后。 */
const pickInlineFromLibrary = (asset) =>
  run(
    'inline-pick',
    () => studioExtraApi.pickInline(props.article.id, asset.id, props.selectedBlockId),
    '插图已从素材图库插入正文',
  )

const removeInline = (item) =>
  run('inline-remove', () => studioExtraApi.removeInline(props.article.id, item.id), '插图已移除')

const makeSvgCard = (kind, blockIds) =>
  run(
    'svg',
    () => studioExtraApi.svgCard(props.article.id, props.selectedBlockId, kind, blockIds),
    blockIds?.length > 1 ? 'SVG 卡已生成，插在所选最后一处段落之后' : 'SVG 卡已插入该段之后',
  )

// ---- AI 拟提示词 ----

/**
 * 按当前稿件现拟提示词。
 * scope=cover 出封面候选；scope=inline 且带 blockId 只拟这一段，
 * 不带 blockId 就通读全文，挑出该配图的段落各给一条。
 */
async function suggestPrompts(scope, blockId = '') {
  if (!props.article?.id || busyAction.value) return
  action.value = blockId ? 'suggest-inline-one' : scope === 'cover' ? 'suggest-cover' : 'suggest-inline-all'
  error.value = ''
  try {
    const payload = await studioExtraApi.suggestPrompts(props.article.id, {
      scope,
      count: scope === 'cover' ? 3 : 4,
      ...(blockId ? { blockId } : {}),
    })
    suggestions.value = payload?.items || []
    suggestScope.value = scope
    if (!suggestions.value.length) {
      emit('notify', '模型没有给出可用的提示词，换个说法再试一次。')
    }
  } catch (err) {
    error.value = err.message || '生成提示词失败，请重试'
    emit('notify', error.value)
  } finally {
    action.value = ''
  }
}

/** 点一条建议：封面直接填进封面提示词；插图还要把目标段落切到建议指向的那段。 */
function useSuggestion(item) {
  if (suggestScope.value === 'cover') {
    coverPrompt.value = item.prompt
    return
  }
  inlinePrompt.value = item.prompt
  if (item.blockId && item.blockId !== props.selectedBlockId) {
    emit('select-block', item.blockId)
  }
}

const coverSuggestions = computed(() => (suggestScope.value === 'cover' ? suggestions.value : []))
const inlineSuggestions = computed(() => (suggestScope.value === 'inline' ? suggestions.value : []))

watch(
  () => props.article?.id,
  () => {
    stopPolling()
    coverPrompt.value = props.article?.cover?.prompt || ''
    inlinePrompt.value = ''
    error.value = ''
    suggestions.value = []
    suggestScope.value = ''
    loadCatalog()
    loadPrompts()
    loadCapabilities()
    ensurePolling()
  },
  { immediate: true },
)

// 每次快照替换后重算「还有没有在跑的」，没有就自动停表
watch(() => props.article, ensurePolling)

onBeforeUnmount(() => {
  disposed = true
  stopPolling()
})
</script>

<template>
  <section class="media-panel" aria-label="封面与插图">
    <header class="panel-head">
      <span>封面 · 插图</span>
      <span class="panel-note">{{ runningInline || coverStatus === 'running' ? '生成中…' : '自动刷新' }}</span>
    </header>

    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
    <p v-if="!article" class="panel-empty">左侧选一篇稿件。</p>

    <template v-else>
      <section class="block-section">
        <div class="setting-head">
          <h3>封面</h3>
          <span class="state" :class="coverStatus">{{ statusLabel[coverStatus] || coverStatus }}</span>
        </div>

        <div v-if="coverImage" class="cover-preview">
          <button type="button" class="image-button" aria-label="查看封面大图" @click="previewImage = coverImage"><img :src="coverImage" alt="封面预览" /></button>
        </div>
        <div v-else-if="coverStatus === 'running'" class="cover-skeleton" role="status">
          <span class="shimmer" />
          <p>正在生成封面，每 2 秒自动检查一次结果…</p>
        </div>
        <p v-else-if="coverStatus === 'failed'" class="panel-error" role="alert">
          {{ coverError || '封面生成失败，换个提示词再试。' }}
        </p>

        <!-- 选素材图的入口常驻：还没封面时也要能直接从图库里挑一张 -->
        <div class="row cover-actions">
          <NButton size="tiny" :disabled="busyAction" :loading="isAction('cover-pick')" @click="assetPicker = 'cover'">
            从素材图库选
          </NButton>
          <NButton v-if="coverImage" size="tiny" :disabled="busyAction || !capabilities?.ratios?.includes(coverRatio)" :loading="isAction('cover')" @click="generateCover">
            换一张
          </NButton>
          <NButton v-if="coverImage" size="tiny" :disabled="busyAction" :loading="isAction('cover-remove')" @click="removeCover">
            移除
          </NButton>
        </div>

        <NSelect :value="null" size="small" filterable :options="coverPromptOptions" placeholder="从提示词库选择封面模板" @update:value="(value) => applyPrompt(value, 'cover')" />
        <p v-if="promptError" class="hint strong">{{ promptError }}</p>
        <label class="field">
          <span>提示词（选择后可修改，不影响提示词库）</span>
          <NInput
            :value="coverPrompt"
            size="small"
            type="textarea"
            :rows="2"
            maxlength="600"
            placeholder="留空则按标题 + 正文首段自动拟"
            @update:value="(value) => (coverPrompt = value)"
          />
        </label>
        <NButton
          size="small"
          secondary
          class="suggest-btn"
          :disabled="busyAction"
          :loading="isAction('suggest-cover')"
          @click="suggestPrompts('cover')"
        >
          按标题与内容生成封面提示词
        </NButton>
        <ul v-if="coverSuggestions.length" class="suggest-list">
          <li v-for="(item, index) in coverSuggestions" :key="index">
            <button type="button" class="suggest-item" @click="useSuggestion(item)">
              <span class="suggest-text">{{ item.prompt }}</span>
              <span v-if="item.note" class="suggest-note">{{ item.note }}</span>
            </button>
          </li>
        </ul>
        <NSelect v-model:value="coverStyle" size="small" :options="styles" aria-label="封面风格" />
        <p class="hint">{{ capabilityError || (capabilities?.ratios?.length ? '默认比例不可用时，请选择列表中已确认的比例。' : '当前模型没有已登记的精确比例，请在模型管理编辑默认生图模型并登记供应商支持的尺寸后重新打开；环境变量供应商无法登记，请改选已配置的默认模型。') }}</p>
        <div class="row">
          <NSelect
            :value="coverRatio"
            size="small"
            class="ratio"
            :options="aspectOptions"
            :disabled="busyAction"
            @update:value="(value) => (coverRatio = value)"
          />
          <NButton
            size="small"
            type="primary"
            class="grow"
            :disabled="busyAction || !capabilities?.ratios?.includes(coverRatio)"
            :loading="isAction('cover')"
            @click="generateCover"
          >
            {{ coverStatus === 'ready' ? '重新生成' : '生成封面' }}
          </NButton>
        </div>
      </section>

      <section class="block-section">
        <div class="setting-head">
          <h3>正文插图</h3>
          <span class="state" :class="runningInline ? 'running' : inlineItems.length ? 'ready' : 'idle'">
            {{ runningInline ? `${runningInline} 张生成中` : `${inlineItems.length} 张` }}
          </span>
        </div>

        <p v-if="!selectedBlockId" class="hint strong">先在稿纸里点一段，再给这一段配图。</p>
        <p v-else class="hint">
          目标段落 <b>第 {{ blockIndex(selectedBlockId) }} 段</b>：{{ selectedExcerpt || '（空段落）' }}
        </p>

        <div class="row">
          <NButton
            size="small"
            secondary
            :disabled="busyAction || !selectedBlockId"
            :loading="isAction('suggest-inline-one')"
            @click="suggestPrompts('inline', selectedBlockId)"
          >
            为这一段生成提示词
          </NButton>
          <NButton
            size="small"
            secondary
            :disabled="busyAction"
            :loading="isAction('suggest-inline-all')"
            @click="suggestPrompts('inline')"
          >
            智能规划整篇插图
          </NButton>
        </div>
        <ul v-if="inlineSuggestions.length" class="suggest-list">
          <li v-for="(item, index) in inlineSuggestions" :key="index">
            <button type="button" class="suggest-item" @click="useSuggestion(item)">
              <span v-if="item.blockNumber" class="suggest-block">第 {{ item.blockNumber }} 段</span>
              <span class="suggest-text">{{ item.prompt }}</span>
              <span v-if="item.note" class="suggest-note">{{ item.note }}</span>
            </button>
          </li>
        </ul>

        <NSelect :value="null" size="small" filterable :options="inlinePromptOptions" placeholder="从提示词库选择插图模板" @update:value="(value) => applyPrompt(value, 'inline')" />
        <p v-if="promptError" class="hint strong">{{ promptError }}</p>
        <label class="field">
          <span>画面描述（选择后可修改，不影响提示词库）</span>
          <NInput
            :value="inlinePrompt"
            size="small"
            type="textarea"
            :rows="2"
            maxlength="600"
            placeholder="想要的场景、风格或构图的提示词"
            @update:value="(value) => (inlinePrompt = value)"
          />
        </label>
        <NSelect v-model:value="inlineStyle" size="small" :options="styles" aria-label="插图风格" />
        <p class="hint">{{ capabilityError || (capabilities?.ratios?.length ? '默认比例不可用时，请选择列表中已确认的比例。' : '当前模型没有已登记的精确比例，请在模型管理编辑默认生图模型并登记供应商支持的尺寸后重新打开；环境变量供应商无法登记，请改选已配置的默认模型。') }}</p>
        <div class="row">
          <NSelect
            :value="inlineRatio"
            size="small"
            class="ratio"
            :options="aspectOptions"
            :disabled="busyAction"
            @update:value="(value) => (inlineRatio = value)"
          />
          <NButton
            size="small"
            type="primary"
            class="grow"
            :disabled="busyAction || !selectedBlockId || !capabilities?.ratios?.includes(inlineRatio)"
            :loading="isAction('inline')"
            @click="generateInline"
          >
            为这一段配图
          </NButton>
        </div>
        <NButton
          size="small"
          secondary
          class="library-btn"
          :disabled="busyAction || !selectedBlockId"
          :loading="isAction('inline-pick')"
          @click="assetPicker = 'inline'"
        >
          {{ selectedBlockId ? `从素材图库选一张插到第 ${blockIndex(selectedBlockId)} 段` : '从素材图库选一张插图' }}
        </NButton>

        <ul v-if="inlineItems.length" class="diagram-list">
          <li v-for="item in inlineItems" :key="item.id" class="diagram-item">
            <button v-if="item.status === 'ready' && item.url" type="button" class="image-button" aria-label="查看插图大图" @click="previewImage = apiUrl(item.url)"><img :src="apiUrl(item.url)" alt="正文插图" /></button>
            <div v-else class="diagram-placeholder" :class="item.status">
              {{ statusLabel[item.status] || item.status }}
            </div>
            <div class="diagram-meta">
              <span>第 {{ blockIndex(item.blockId) || '?' }} 段</span>
              <span class="state" :class="item.status">{{ statusLabel[item.status] || item.status }}</span>
              <small v-if="item.error" class="diagram-error">{{ item.error }}</small>
            </div>
            <NButton
              size="tiny"
              quaternary
              :disabled="busyAction"
              :loading="isAction('inline-remove')"
              @click="removeInline(item)"
            >移除</NButton>
          </li>
        </ul>
        <p v-else-if="selectedBlockId" class="hint">这一段还没有插图。</p>
      </section>

      <PaperSvgCardPicker
        :catalog="catalog"
        :selected-block-id="selectedBlockId"
        :selected-block-ids="sourceBlockIds"
        :blocks="blocks"
        :busy="busyAction"
        :loading="isAction('svg')"
        @confirm="makeSvgCard"
        @pick-in-paper="emit('pick-in-paper')"
      />

      <PaperAssetPicker
        :show="assetPicker === 'cover'"
        title="从素材图库选封面"
        hint="点任意图片即设为封面；这张图不占用生图额度。"
        @update:show="(show) => { if (!show) assetPicker = '' }"
        @pick="pickCoverFromLibrary"
      />
      <PaperAssetPicker
        :show="assetPicker === 'inline'"
        title="从素材图库选插图"
        :hint="selectedBlockId ? `选中的图会插到第 ${blockIndex(selectedBlockId)} 段之后。` : '选中的图会插到你正在编辑的那一段之后。'"
        @update:show="(show) => { if (!show) assetPicker = '' }"
        @pick="pickInlineFromLibrary"
      />
    </template>
    <NModal :show="Boolean(previewImage)" preset="card" title="图片预览" style="width: min(900px, 95vw)" @update:show="(show) => { if (!show) previewImage = '' }">
      <img v-if="previewImage" class="large-preview" :src="previewImage" alt="生成图片大图" />
      <template #footer><NButton :loading="downloading" @click="downloadPreview">下载图片</NButton></template>
    </NModal>
  </section>
</template>

<style scoped>
.media-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: var(--color-surface);
}
.panel-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
}
.panel-note { font-size: 11px; font-weight: 400; color: var(--color-text-tertiary); }
.block-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--color-border);
}
.block-section:first-of-type { padding-top: 0; border-top: 0; }
.setting-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.setting-head h3 { margin: 0; font-size: 12px; font-weight: 600; }
.state { font-size: 10px; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--color-border); color: var(--color-text-tertiary); }
.state.running { color: var(--paper-warning); border-color: var(--paper-warning-line); background: var(--paper-warning-soft); }
.state.ready { color: var(--paper-success); border-color: var(--paper-success); }
.state.failed { color: var(--paper-danger); border-color: var(--paper-danger-line); background: var(--paper-danger-soft); }

.image-button { display: block; padding: 0; border: 0; background: transparent; cursor: zoom-in; }
.cover-preview .image-button { width: 100%; }
.large-preview { display: block; max-width: 100%; max-height: 70vh; margin: auto; object-fit: contain; }
.cover-preview { display: flex; flex-direction: column; gap: 6px; }
.cover-preview img {
  width: 100%;
  max-height: 240px;
  object-fit: contain;
  border-radius: 8px;
  border: 1px solid var(--paper-line);
  background: var(--color-surface-muted);
}
.cover-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.cover-skeleton {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px;
  border: 1px dashed var(--paper-line-strong);
  border-radius: 8px;
  font-size: 11px;
  color: var(--color-text-tertiary);
}
.cover-skeleton p { margin: 0; }
.shimmer {
  display: block;
  height: 54px;
  border-radius: 6px;
  background: var(--color-surface-muted);
  animation: media-pulse 1.2s infinite;
}

.field { display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: var(--color-text-tertiary); }
.row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.ratio { width: 92px; flex: none; }
.grow { flex: 1; }
.suggest-btn { align-self: flex-start; }
.library-btn { align-self: flex-start; }

/* AI 候选：一条一段说明，点一下就填进上面的输入框 */
.suggest-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.suggest-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  padding: 7px 9px;
  text-align: left;
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--paper-accent);
  border-radius: 8px;
  background: var(--color-surface-muted);
  cursor: pointer;
  color: inherit;
}
.suggest-item:hover { border-color: var(--paper-accent); background: var(--paper-accent-tint); }
.suggest-text { font-size: 11px; line-height: 1.6; color: var(--color-text-secondary); }
.suggest-note { font-size: 10px; color: var(--color-text-tertiary); }
.suggest-block {
  align-self: flex-start;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--paper-accent);
  color: var(--paper-on-accent);
  font-size: 10px;
  line-height: 16px;
}

.diagram-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.diagram-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
}
.diagram-item img {
  width: 52px;
  height: 52px;
  object-fit: cover;
  border-radius: 6px;
  flex: none;
  background: var(--color-surface-muted);
}
.diagram-placeholder {
  width: 52px;
  height: 52px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  font-size: 10px;
  color: var(--color-text-tertiary);
  background: var(--color-surface-muted);
}
.diagram-placeholder.running { animation: media-pulse 1.2s infinite; }
.diagram-placeholder.failed { color: var(--paper-danger); background: var(--paper-danger-soft); }
.diagram-meta { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; font-size: 11px; color: var(--color-text-tertiary); }
.diagram-error { color: var(--paper-danger); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.hint { margin: 0; font-size: 11px; line-height: 1.6; color: var(--color-text-tertiary); }
.hint.strong { color: var(--color-text-secondary); }
.panel-error {
  margin: 0;
  padding: 8px 10px;
  border-radius: 8px;
  font-size: 12px;
  color: var(--paper-danger);
  background: var(--paper-danger-soft);
  border: 1px solid var(--paper-danger-line);
}
.panel-empty { margin: 0; font-size: 12px; color: var(--color-text-tertiary); }
@keyframes media-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.45; }
}
</style>
