<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NButton, NInput, NInputNumber } from 'naive-ui'
import { toPng } from 'html-to-image'
import JSZip from 'jszip'
import { api } from '../../api/client'
import CardCanvas from './CardCanvas.vue'
import BackendContractModal from './BackendContractModal.vue'
import MarkdownEditor from '../../components/MarkdownEditor.vue'
import CardPageBreak from './CardPageBreakExtension.js'
import {
  BACKGROUND_SWATCHES,
  BORDER_OPTIONS,
  BUILTIN_TEMPLATES,
  DEFAULT_MARKDOWN_CONTENT,
  DEFAULT_META,
  FONT_OPTIONS,
  PATTERN_OPTIONS,
  PLATFORM_PRESETS,
  PRESET_MARKDOWN_SAMPLES,
} from './constants'
import { paginateMarkdownToCards } from './paginator'

const markdown = ref(DEFAULT_MARKDOWN_CONTENT)
const paginationMode = ref('smart-auto')
const activeRatioId = ref('xhs-3-4')
const customWidth = ref(360)
const customHeight = ref(480)
const template = ref({ ...BUILTIN_TEMPLATES[0] })
const customTemplates = ref([])
const newTemplateName = ref('')
const meta = ref({ ...DEFAULT_META })
const rightPanelTab = ref('style')
const previewZoom = ref(1)
const selectedCardIdx = ref(null)
const exportingIndex = ref(null)
const statusMessage = ref(null)
const modalOpen = ref(false)
const engineMode = ref('frontend')
const serverCards = ref(null)
const lastServerSyncAt = ref(null)
const isSyncing = ref(false)
const savingTemplate = ref(false)
const templateError = ref('')

const markdownInput = ref(null)
const cardEditorRef = ref(null)  // MarkdownEditor 暴露的 tiptap editor 实例
const fromStudio = ref(Boolean(history.state?.markdown))  // 是否从文章台跳转而来
const previewArea = ref(null)
const mdImageInput = ref(null)
const bgTextureInput = ref(null)
const borderOverlayInput = ref(null)
let statusTimer = null

const activePreset = computed(() => {
  const found = PLATFORM_PRESETS.find((item) => item.id === activeRatioId.value) || PLATFORM_PRESETS[0]
  return {
    ...found,
    width: customWidth.value || found.width,
    height: customHeight.value || found.height,
  }
})

const localCards = computed(() =>
  paginateMarkdownToCards(
    markdown.value,
    activePreset.value,
    template.value,
    meta.value,
    paginationMode.value,
  ),
)

const cards = computed(() =>
  engineMode.value === 'backend-api' && serverCards.value ? serverCards.value : localCards.value,
)

const allTemplates = computed(() => [...customTemplates.value, ...BUILTIN_TEMPLATES])

const exportSizeLabel = computed(
  () =>
    `${activePreset.value.width * activePreset.value.exportScale}×${
      activePreset.value.height * activePreset.value.exportScale
    }px`,
)

function flash(message) {
  statusMessage.value = message
  clearTimeout(statusTimer)
  statusTimer = setTimeout(() => {
    statusMessage.value = null
  }, 3200)
}

function selectPreset(presetId) {
  activeRatioId.value = presetId
  const target = PLATFORM_PRESETS.find((item) => item.id === presetId)
  if (target) {
    customWidth.value = target.width
    customHeight.value = target.height
  }
}

function setCustomWidth(value) {
  customWidth.value = Math.max(280, Math.min(640, Number(value) || 360))
}

function setCustomHeight(value) {
  customHeight.value = Math.max(320, Math.min(800, Number(value) || 480))
}

function patchTemplate(patch) {
  template.value = { ...template.value, ...patch }
}

function patchMeta(patch) {
  meta.value = { ...meta.value, ...patch }
}

function insertAtCursor(snippet) {
  // 优先通过 tiptap editor 插入（保留光标位置语义）
  if (cardEditorRef.value) {
    cardEditorRef.value.chain().focus().insertContent(snippet).run()
    return
  }
  // fallback：追加到末尾
  markdown.value = `${markdown.value}\n${snippet}`
}

function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

async function onInsertLocalImage(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  const dataUrl = await readAsDataUrl(file)
  const name = file.name.replace(/\.[^.]+$/, '')
  insertAtCursor(`\n![${name}](${dataUrl})\n`)
}

async function onUploadBgTexture(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  const dataUrl = await readAsDataUrl(file)
  patchTemplate({ customBgImage: dataUrl, bgImageOpacity: template.value.bgImageOpacity ?? 0.45 })
}

async function onUploadBorderImage(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  patchTemplate({ customBorderImage: await readAsDataUrl(file) })
}

function templateConfig() {
  const { id, name, subtitle, isCustom, ...config } = template.value
  return config
}

async function loadCustomTemplates() {
  try {
    const rows = await api.get('/cards/templates')
    customTemplates.value = rows.map((row) => ({
      ...row.config,
      id: row.id,
      name: row.name,
      subtitle: row.subtitle,
      isCustom: true,
    }))
  } catch (err) {
    templateError.value = err.message
  }
}

async function saveCustomTemplate() {
  if (savingTemplate.value) return
  const title = newTemplateName.value.trim() || `自定义模板 ${customTemplates.value.length + 1}`
  const subtitle = `${template.value.patternType !== 'none' ? '含底纹' : '纯底'} · ${
    template.value.customBorderImage ? '自定义边框图' : template.value.borderFrameType
  }`
  savingTemplate.value = true
  templateError.value = ''
  try {
    const created = await api.post('/cards/templates', {
      name: title,
      subtitle,
      config: templateConfig(),
    })
    const saved = {
      ...created.config,
      id: created.id,
      name: created.name,
      subtitle: created.subtitle,
      isCustom: true,
    }
    customTemplates.value = [saved, ...customTemplates.value]
    template.value = saved
    newTemplateName.value = ''
    flash(`已保存模板「${title}」`)
  } catch (err) {
    templateError.value = err.message
  } finally {
    savingTemplate.value = false
  }
}

async function deleteCustomTemplate(id) {
  try {
    await api.del(`/cards/templates/${id}`)
    customTemplates.value = customTemplates.value.filter((item) => item.id !== id)
    if (template.value.id === id) template.value = { ...BUILTIN_TEMPLATES[0] }
  } catch (err) {
    templateError.value = err.message
  }
}

function cardNode(index) {
  return previewArea.value?.querySelector(`[data-card-index="${index}"]`) || null
}

async function exportSingleCard(index) {
  const node = cardNode(index)
  if (!node) return
  exportingIndex.value = index
  try {
    await document.fonts.ready
    const dataUrl = await toPng(node, {
      cacheBust: true,
      pixelRatio: activePreset.value.exportScale,
      width: activePreset.value.width,
      height: activePreset.value.height,
    })
    const link = document.createElement('a')
    link.download = `MarkCard_${activePreset.value.id}_P${String(index).padStart(2, '0')}.png`
    link.href = dataUrl
    link.click()
    flash(`已导出第 ${index} 张卡片（${exportSizeLabel.value}）`)
  } catch {
    flash('导出图片失败，请检查自定义图片跨域设置')
  } finally {
    exportingIndex.value = null
  }
}

async function exportAllCardsZip() {
  if (cards.value.length === 0) return
  exportingIndex.value = 'all'
  flash(`正在渲染 ${cards.value.length} 张高清卡片...`)
  try {
    await document.fonts.ready
    const zip = new JSZip()
    const folder = zip.folder(`MarkCard_${activePreset.value.id}_${Date.now()}`)
    for (const card of cards.value) {
      const node = cardNode(card.index)
      if (!node) continue
      const dataUrl = await toPng(node, {
        cacheBust: true,
        pixelRatio: activePreset.value.exportScale,
        width: activePreset.value.width,
        height: activePreset.value.height,
      })
      folder.file(
        `Card_${String(card.index).padStart(2, '0')}_${activePreset.value.width * activePreset.value.exportScale}x${
          activePreset.value.height * activePreset.value.exportScale
        }.png`,
        dataUrl.replace(/^data:image\/png;base64,/, ''),
        { base64: true },
      )
    }
    folder.file(
      'card_manifest.json',
      JSON.stringify(
        {
          exportedAt: new Date().toISOString(),
          platform: activePreset.value,
          template: template.value,
          meta: meta.value,
          cards: cards.value,
        },
        null,
        2,
      ),
    )
    const blob = await zip.generateAsync({ type: 'blob' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `MarkCard_${activePreset.value.platformLabel.replace(/\s+/g, '_')}_${
      cards.value.length
    }张图集.zip`
    link.click()
    URL.revokeObjectURL(url)
    flash(`成功导出全部 ${cards.value.length} 张高清卡片 ZIP 图集`)
  } catch {
    flash('批量导出失败，请尝试单张导出')
  } finally {
    exportingIndex.value = null
  }
}

async function syncWithBackendApi() {
  isSyncing.value = true
  try {
    const data = await api.post('/cards/paginate', {
      markdown: markdown.value,
      presetId: activePreset.value.id,
      customWidth: activePreset.value.width,
      customHeight: activePreset.value.height,
      customTemplate: template.value,
      meta: meta.value,
      paginationMode: paginationMode.value,
    })
    if (Array.isArray(data.cards)) {
      serverCards.value = data.cards
      const now = new Date()
      lastServerSyncAt.value = now.toLocaleTimeString('zh-CN', { hour12: false })
    }
  } catch {
    // 后端不可用时保留本地卡片，不打断编辑
  } finally {
    isSyncing.value = false
  }
}

let syncTimer = null
watch(
  [engineMode, markdown, activePreset, template, meta, paginationMode],
  () => {
    if (engineMode.value !== 'backend-api') return
    clearTimeout(syncTimer)
    syncTimer = setTimeout(syncWithBackendApi, 280)
  },
  { deep: true },
)

function changeEngineMode(mode) {
  engineMode.value = mode
  if (mode === 'backend-api') syncWithBackendApi()
}

function occupancy(card) {
  return Math.min(100, Math.round((card.estimatedUsedHeight / card.maxAvailableHeight) * 100))
}

onMounted(() => {
  loadCustomTemplates()
  // 从文章台跳转过来时，预填 markdown 内容
  const stateMarkdown = history.state?.markdown
  if (stateMarkdown && typeof stateMarkdown === 'string') {
    markdown.value = stateMarkdown
  }
})
onBeforeUnmount(() => {
  clearTimeout(statusTimer)
  clearTimeout(syncTimer)
})
</script>

<template>
  <div class="studio">
    <input ref="mdImageInput" type="file" accept="image/*" hidden @change="onInsertLocalImage" />
    <input ref="bgTextureInput" type="file" accept="image/*" hidden @change="onUploadBgTexture" />
    <input ref="borderOverlayInput" type="file" accept="image/*" hidden @change="onUploadBorderImage" />

    <header class="studio-bar">
      <div class="preset-tabs">
        <button
          v-for="preset in PLATFORM_PRESETS"
          :key="preset.id"
          class="preset-tab"
          :class="{ 'is-active': activeRatioId === preset.id }"
          @click="selectPreset(preset.id)"
        >
          {{ preset.name }}（{{ preset.ratioLabel.replace(/\s+/g, '') }}）
        </button>
      </div>
      <n-button size="small" type="primary" :disabled="exportingIndex !== null" :loading="exportingIndex === 'all'" @click="exportAllCardsZip">
        {{ exportingIndex === 'all' ? '正在打包图集...' : `批量导出图集（${cards.length}张）` }}
      </n-button>
    </header>

    <!-- 从文章台跳转而来时显示返回提示 -->
    <div v-if="fromStudio" class="studio-from-banner">
      <span>已将文章正文导入卡片台</span>
      <button class="link-btn" @click="$router.push({ name: 'studio' })">← 返回创作台</button>
    </div>

    <main class="studio-grid">
      <!-- 左：编辑器 -->
      <section class="panel panel-editor">
        <div class="panel-head">
          <span class="panel-title">Markdown 源文件</span>
          <span class="mono-muted">{{ markdown.length }} 字符</span>
          <span class="spacer-flex" />
          <span class="tiny-muted">范例:</span>
          <button
            v-for="sample in PRESET_MARKDOWN_SAMPLES"
            :key="sample.label"
            class="chip"
            @click="markdown = sample.content"
          >
            {{ sample.short }}
          </button>
        </div>

        <div class="toolbar">
          <button class="tool-btn" @click="insertAtCursor('\n\n---\n\n')">
            插入分页符 (---)
          </button>
          <button
            class="tool-btn"
            @click="insertAtCursor('\n\n---\n\n## 新卡片章节标题\n\n在这里输入新卡片正文内容...\n')"
          >
            插入卡片模板
          </button>
          <button class="tool-btn" @click="insertAtCursor(' ==重点高亮文字== ')">高亮标记</button>
          <button
            class="tool-btn"
            @click="
              insertAtCursor(
                '\n| 对比维度 | 方案 A | 方案 B |\n| :--- | :--- | :--- |\n| 核心优势 | 结构清晰 | 视觉吸睛 |\n| 转化效率 | +45% | +120% |\n',
              )
            "
          >
            插入表格
          </button>
          <button class="tool-btn" @click="mdImageInput?.click()">插入本地图片</button>
        </div>

        <MarkdownEditor
          v-model="markdown"
          :extensions="[CardPageBreak]"
          :toolbar="{ image: false, table: false }"
          :enable-ai-bubble="false"
          placeholder="在此输入 Markdown 内容（支持 # 标题、**加粗**、==高亮==、| 表格 |、> 引用、列表及 --- 分页符）..."
          class="card-studio-editor"
          @editor-ready="(ed) => (cardEditorRef = ed)"
        />

        <div class="panel-foot">
          <span class="tiny-muted">
            已拆分为 <strong>{{ cards.length }}</strong> 张卡片
          </span>
          <button class="link-btn" @click="markdown = DEFAULT_MARKDOWN_CONTENT">重置示例</button>
        </div>
      </section>

      <!-- 中：预览 -->
      <section class="panel panel-preview">
        <div class="panel-head">
          <span class="panel-title">{{ activePreset.platformLabel }}</span>
          <span class="mono-muted">画布 {{ activePreset.width }}×{{ activePreset.height }}px</span>
          <span class="mono-accent">导出 {{ exportSizeLabel }}</span>
          <span class="spacer-flex" />
          <button
            class="chip"
            :class="{ 'is-on': meta.showSafeZoneGuide }"
            @click="patchMeta({ showSafeZoneGuide: !meta.showSafeZoneGuide })"
          >
            安全框线
          </button>
          <div class="zoom-group">
            <button
              v-for="z in [0.85, 1]"
              :key="z"
              class="zoom-btn"
              :class="{ 'is-active': previewZoom === z }"
              @click="previewZoom = z"
            >
              {{ Math.round(z * 100) }}%
            </button>
          </div>
        </div>

        <div v-if="statusMessage" class="toast">{{ statusMessage }}<span class="toast-tail">3x HD PNG</span></div>

        <div class="card-filter">
          <button class="chip" :class="{ 'is-solid': selectedCardIdx === null }" @click="selectedCardIdx = null">
            全部卡片（{{ cards.length }}）
          </button>
          <button
            v-for="c in cards"
            :key="c.index"
            class="chip mono"
            :class="{ 'is-solid': selectedCardIdx === c.index }"
            @click="selectedCardIdx = c.index"
          >
            P{{ String(c.index).padStart(2, '0') }}
          </button>
          <span class="spacer-flex" />
          <button class="link-btn mono" @click="modalOpen = true">
            引擎: {{ engineMode === 'frontend' ? '纯前端' : '/api/v1/cards/paginate' }}
          </button>
        </div>

        <div ref="previewArea" class="preview-area">
          <div
            v-for="card in cards"
            v-show="selectedCardIdx === null || selectedCardIdx === card.index"
            :key="card.index"
            class="preview-item"
            :style="{ width: `${activePreset.width * previewZoom}px` }"
          >
            <div class="preview-meta">
              <span class="mono-strong">CARD {{ String(card.index).padStart(2, '0') }} / {{ String(card.total).padStart(2, '0') }}</span>
              <span class="tiny-muted">版面占用 {{ occupancy(card) }}%</span>
              <span v-if="card.isOverflowing" class="warn-text">建议开启智能高度分页</span>
              <span class="spacer-flex" />
              <button class="link-btn" :disabled="exportingIndex !== null" @click="exportSingleCard(card.index)">
                {{ exportingIndex === card.index ? '导出中...' : '导出单张 PNG' }}
              </button>
            </div>
            <div
              class="card-frame"
              :style="{
                width: `${activePreset.width * previewZoom}px`,
                height: `${activePreset.height * previewZoom}px`,
              }"
            >
              <div :style="{ transform: `scale(${previewZoom})`, transformOrigin: 'top left' }">
                <CardCanvas
                  :card="card"
                  :preset="activePreset"
                  :template="template"
                  :meta="meta"
                  :is-exporting="exportingIndex !== null"
                />
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 右：模板 / 图层 / 布局 -->
      <aside class="panel panel-inspector">
        <div class="inspector-tabs">
          <button class="seg-btn" :class="{ 'is-active': rightPanelTab === 'style' }" @click="rightPanelTab = 'style'">风格</button>
          <button class="seg-btn" :class="{ 'is-active': rightPanelTab === 'layout' }" @click="rightPanelTab = 'layout'">布局</button>
          <button class="seg-btn" :class="{ 'is-active': rightPanelTab === 'info' }" @click="rightPanelTab = 'info'">信息</button>
        </div>

        <div class="inspector-body">
          <p v-if="templateError" class="error-text">{{ templateError }}</p>

          <!-- 风格 Tab：模板库 + 背景/底纹/边框 + 字色 -->
          <template v-if="rightPanelTab === 'style'">
            <div class="section-head">
              <strong>主题模板（{{ allTemplates.length }}）</strong>
              <span class="tiny-muted">点击实时切换</span>
            </div>
            <div class="template-list">
              <div
                v-for="tpl in allTemplates"
                :key="tpl.id"
                class="template-item"
                :class="{ 'is-selected': template.id === tpl.id }"
              >
                <button class="template-pick" @click="template = { ...tpl }">
                  <span
                    class="swatch"
                    :style="{ background: tpl.bgStyle || tpl.bgColorHex, borderColor: tpl.borderColor }"
                  >
                    <span :style="{ color: tpl.textColor }">Aa</span>
                    <span class="swatch-dot" :style="{ backgroundColor: tpl.accentColor }" />
                  </span>
                  <span class="template-text">
                    <span class="template-name">
                      {{ tpl.name }}
                      <em v-if="tpl.isCustom">· 自建</em>
                    </span>
                    <span class="template-sub">{{ tpl.subtitle }}</span>
                  </span>
                </button>
                <button
                  v-if="tpl.isCustom"
                  class="icon-link danger"
                  title="删除自定义模板"
                  @click="deleteCustomTemplate(tpl.id)"
                >
                  删除
                </button>
                <span v-else-if="template.id === tpl.id" class="check-mark">✓</span>
              </div>
            </div>

            <div class="divider" />
            <div class="section-head">
              <strong>背景色</strong>
              <input
                type="color"
                class="color-input"
                :value="template.bgColorHex"
                title="自定义背景纯色"
                @input="patchTemplate({ bgColorHex: $event.target.value, bgStyle: $event.target.value })"
              />
            </div>
            <div class="swatch-grid">
              <button
                v-for="bg in BACKGROUND_SWATCHES"
                :key="bg.label"
                class="chip"
                @click="patchTemplate({ bgStyle: bg.bg, bgColorHex: bg.bg, textColor: bg.text })"
              >
                <span class="dot" :style="{ backgroundColor: bg.bg }" />{{ bg.label }}
              </button>
            </div>
            <div class="inline-row">
              <button class="chip grow" @click="bgTextureInput?.click()">
                {{ template.customBgImage ? '更换自定义背景图' : '上传自定义底纹背景图' }}
              </button>
              <button v-if="template.customBgImage" class="chip danger" @click="patchTemplate({ customBgImage: undefined })">
                清除
              </button>
            </div>
            <div v-if="template.customBgImage" class="slider-row">
              <span>背景图透明度</span>
              <span class="mono-muted">{{ Math.round((template.bgImageOpacity ?? 0.4) * 100) }}%</span>
              <input
                type="range"
                min="0.05"
                max="1"
                step="0.05"
                :value="template.bgImageOpacity ?? 0.4"
                @input="patchTemplate({ bgImageOpacity: parseFloat($event.target.value) })"
              />
            </div>

            <div class="divider" />
            <div class="section-head">
              <strong>几何底纹</strong>
              <input
                type="color"
                class="color-input"
                :value="template.patternColor"
                title="底纹线条颜色"
                @input="patchTemplate({ patternColor: $event.target.value })"
              />
            </div>
            <div class="option-grid">
              <button
                v-for="pat in PATTERN_OPTIONS"
                :key="pat.id"
                class="chip"
                :class="{ 'is-solid': template.patternType === pat.id }"
                @click="patchTemplate({ patternType: pat.id })"
              >
                {{ pat.label }}
              </button>
            </div>
            <div v-if="template.patternType !== 'none'" class="slider-row">
              <span>底纹浓度</span>
              <span class="mono-muted">{{ Math.round(template.patternOpacity * 100) }}%</span>
              <input
                type="range"
                min="0.03"
                max="0.35"
                step="0.01"
                :value="template.patternOpacity"
                @input="patchTemplate({ patternOpacity: parseFloat($event.target.value) })"
              />
            </div>

            <div class="divider" />
            <div class="section-head">
              <strong>边框</strong>
              <input
                type="color"
                class="color-input"
                :value="template.borderColor"
                title="边框颜色"
                @input="patchTemplate({ borderColor: $event.target.value })"
              />
            </div>
            <div class="option-grid">
              <button
                v-for="bdr in BORDER_OPTIONS"
                :key="bdr.id"
                class="chip"
                :class="{ 'is-solid': template.borderFrameType === bdr.id }"
                @click="patchTemplate({ borderFrameType: bdr.id })"
              >
                {{ bdr.label }}
              </button>
            </div>
            <div class="inline-row">
              <button class="chip grow" @click="borderOverlayInput?.click()">
                {{ template.customBorderImage ? '已叠加自定义边框图（点击更换）' : '上传自定义透明边框 PNG/SVG' }}
              </button>
              <button v-if="template.customBorderImage" class="chip danger" @click="patchTemplate({ customBorderImage: undefined })">
                移除
              </button>
            </div>

            <div class="divider" />
            <div class="section-head"><strong>文字颜色</strong></div>
            <div class="inline-row">
              <label class="inline-color">
                正文颜色
                <input type="color" class="color-input" :value="template.textColor" @input="patchTemplate({ textColor: $event.target.value })" />
              </label>
              <label class="inline-color">
                高亮强调色
                <input
                  type="color"
                  class="color-input"
                  :value="template.accentColor"
                  @input="patchTemplate({ accentColor: $event.target.value, accentBgSoft: `${$event.target.value}1F` })"
                />
              </label>
            </div>

            <div class="divider" />
            <label class="field-label">保存当前风格为新模板</label>
            <div class="inline-row">
              <n-input v-model:value="newTemplateName" placeholder="输入自定义模板名称..." />
              <n-button size="small" type="primary" :disabled="savingTemplate" :loading="savingTemplate" @click="saveCustomTemplate">
                {{ savingTemplate ? '保存中...' : '保存' }}
              </n-button>
            </div>
          </template>

          <!-- 布局 Tab：尺寸 + 字体 + 字号 + 留白 + 分卡策略 -->
          <template v-else-if="rightPanelTab === 'layout'">
            <span class="section-strong">卡片尺寸（基准 1x / 导出 3x）</span>
            <div class="inline-row">
              <label class="field">
                <span class="field-label">宽度 (px)</span>
                <n-input-number :value="customWidth" :min="280" :max="640" :show-button="false" @update:value="setCustomWidth" />
              </label>
              <label class="field">
                <span class="field-label">高度 (px)</span>
                <n-input-number :value="customHeight" :min="320" :max="800" :show-button="false" @update:value="setCustomHeight" />
              </label>
            </div>

            <div class="divider" />
            <span class="section-strong">字体风格</span>
            <div class="option-grid cols-3">
              <button
                v-for="f in FONT_OPTIONS"
                :key="f.id"
                class="chip"
                :class="{ 'is-solid': template.fontMode === f.id }"
                @click="patchTemplate({ fontMode: f.id })"
              >
                {{ f.label }}
              </button>
            </div>
            <div class="slider-row">
              <span>全局字号比例</span>
              <span class="mono-muted">{{ Math.round(meta.fontSizeScale * 100) }}%</span>
              <input
                type="range"
                min="0.85"
                max="1.15"
                step="0.05"
                :value="meta.fontSizeScale"
                @input="patchMeta({ fontSizeScale: parseFloat($event.target.value) })"
              />
            </div>
            <div class="slider-row">
              <span>安全留白 (Padding)</span>
              <span class="mono-muted">{{ template.paddingX }}px</span>
              <input
                type="range"
                min="18"
                max="44"
                step="2"
                :value="template.paddingX"
                @input="patchTemplate({ paddingX: Number($event.target.value), paddingY: Number($event.target.value) + 2 })"
              />
            </div>

            <div class="divider" />
            <span class="section-strong">分卡策略</span>
            <div class="segmented" style="flex: none; margin-top: 6px">
              <button
                class="seg-btn"
                :class="{ 'is-active': paginationMode === 'smart-auto' }"
                @click="paginationMode = 'smart-auto'"
              >
                智能高度
              </button>
              <button
                class="seg-btn"
                :class="{ 'is-active': paginationMode === 'manual-hr' }"
                @click="paginationMode = 'manual-hr'"
              >
                仅按 ---
              </button>
              <button
                class="seg-btn"
                :class="{ 'is-active': paginationMode === 'by-h2' }"
                @click="paginationMode = 'by-h2'"
              >
                按 H2 分卡
              </button>
            </div>
          </template>

          <!-- 信息 Tab：页眉 + 页脚 + 页码 -->
          <template v-else>
            <div class="section-head">
              <strong>页码</strong>
              <label class="checkbox"><input v-model="meta.showPageNumber" type="checkbox" />显示页码</label>
            </div>

            <div class="divider" />
            <div class="section-head">
              <strong>顶部页眉</strong>
              <label class="checkbox">
                <input v-model="meta.showHeader" type="checkbox" />启用
              </label>
            </div>
            <div v-if="meta.showHeader" class="inline-row">
              <n-input v-model:value="meta.headerLeft" placeholder="左侧栏目名" />
              <n-input v-model:value="meta.headerRight" placeholder="右侧期刊号" />
            </div>

            <div class="divider" />
            <div class="section-head">
              <strong>底部署名</strong>
              <label class="checkbox">
                <input v-model="meta.showFooter" type="checkbox" />启用
              </label>
            </div>
            <div v-if="meta.showFooter" class="inline-row">
              <n-input v-model:value="meta.authorHandle" placeholder="@账号名称" />
              <n-input v-model:value="meta.footerNote" placeholder="底部引导语" />
            </div>
          </template>
        </div>

        <div class="inspector-foot">
          <n-button size="small" quaternary block @click="modalOpen = true">查看后端 JSON 契约</n-button>
        </div>
      </aside>
    </main>

    <BackendContractModal
      :open="modalOpen"
      :engine-mode="engineMode"
      :preset="activePreset"
      :template="template"
      :meta="meta"
      :pagination-mode="paginationMode"
      :cards="cards"
      :last-server-sync-at="lastServerSyncAt"
      :is-syncing="isSyncing"
      @close="modalOpen = false"
      @change-engine-mode="changeEngineMode"
      @trigger-server-test="syncWithBackendApi"
    />
  </div>
</template>

<style scoped>
.studio-from-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px var(--space-5);
  background: var(--color-accent-soft);
  border-bottom: 1px solid var(--color-accent);
  font-size: var(--font-xs);
  color: var(--color-accent);
}

.studio {
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - 56px);
}

.studio-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-5);
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
  position: sticky;
  top: 0;
  z-index: 15;
  flex-wrap: wrap;
}

.preset-tabs {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.preset-tab {
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  padding: 2px 0 4px;
  font-size: var(--font-sm);
  font-weight: 500;
  color: var(--color-text-secondary);
  cursor: pointer;
  white-space: nowrap;
}

.preset-tab:hover {
  color: var(--color-text-primary);
}

.preset-tab.is-active {
  color: var(--color-text-primary);
  border-bottom-color: var(--color-accent);
  font-weight: 600;
}

.studio-grid {
  flex: 1;
  display: grid;
  grid-template-columns: minmax(300px, 0.9fr) minmax(360px, 1.2fr) 320px;
  min-height: 0;
}

.panel {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--color-surface);
}

.panel-editor,
.panel-preview {
  border-right: 1px solid var(--color-border);
}

.panel-preview {
  background: var(--color-bg);
}

.panel-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--color-border);
  font-size: var(--font-xs);
  flex-wrap: wrap;
}

.panel-title {
  font-weight: 600;
  font-size: var(--font-sm);
}

.spacer-flex {
  flex: 1;
}

.mono,
.mono-muted,
.mono-strong,
.mono-accent {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-variant-numeric: tabular-nums;
}

.mono-muted,
.tiny-muted {
  color: var(--color-text-muted);
  font-size: var(--font-xs);
}

.mono-strong {
  font-weight: 600;
  font-size: var(--font-xs);
}

.mono-accent {
  color: var(--color-accent);
  font-size: var(--font-xs);
  font-weight: 500;
}

.toolbar {
  display: flex;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  border-bottom: 1px solid var(--color-border);
  overflow-x: auto;
}

.tool-btn,
.chip,
.link-btn,
.zoom-btn,
.seg-btn {
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
}

.tool-btn {
  padding: 5px 10px;
  font-size: var(--font-xs);
  font-weight: 500;
  color: var(--color-text-secondary);
  background: var(--color-surface-raised);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}

.tool-btn:hover {
  color: var(--color-text-primary);
  border-color: var(--color-border-strong);
}

.mode-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  border-bottom: 1px solid var(--color-border);
}

.segmented,
.zoom-group,
.inspector-tabs {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  background: var(--color-surface-raised);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  flex: 1;
}

.seg-btn,
.zoom-btn {
  padding: 4px 8px;
  font-size: var(--font-xs);
  font-weight: 500;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--color-text-secondary);
  flex: 1;
}

.seg-btn.is-active,
.zoom-btn.is-active {
  background: var(--color-surface);
  color: var(--color-text-primary);
  font-weight: 600;
  box-shadow: var(--shadow-sm);
}

.md-input {
  flex: 1;
  width: 100%;
  min-height: 320px;
  padding: var(--space-4);
  border: none;
  outline: none;
  resize: none;
  background: var(--color-surface);
  color: var(--color-text-primary);
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 13px;
  line-height: 1.7;
}

/* MarkdownEditor 在卡片台中的容器 */
.card-studio-editor {
  flex: 1;
  min-height: calc(100vh - 280px);
}

/* CardPageBreak 可视分页线 */
:deep(.card-page-break-node) {
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 12px 0;
  cursor: pointer;
  user-select: none;
}

:deep(.card-page-break-label) {
  font-size: 11px;
  font-weight: 500;
  color: var(--color-accent);
  letter-spacing: 2px;
  padding: 2px 12px;
  border: 1px dashed var(--color-accent);
  border-radius: 4px;
  opacity: 0.7;
}

:deep(.card-page-break-node:hover .card-page-break-label) {
  opacity: 1;
  background: var(--color-accent-soft);
}
.panel-foot,
.inspector-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  border-top: 1px solid var(--color-border);
}

.link-btn {
  background: none;
  border: none;
  padding: 0;
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
}

.link-btn:hover:not(:disabled) {
  color: var(--color-accent);
}

.link-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  font-size: var(--font-xs);
  font-weight: 500;
  color: var(--color-text-secondary);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}

.chip:hover {
  border-color: var(--color-border-strong);
  color: var(--color-text-primary);
}

.chip.is-solid {
  background: var(--color-text-primary);
  border-color: var(--color-text-primary);
  color: var(--color-surface);
}

.chip.is-on {
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.chip.danger {
  color: var(--color-danger);
  border-color: var(--color-danger);
  background: transparent;
}

.chip.grow {
  flex: 1;
  justify-content: center;
  padding: 8px;
}

.toast {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: 6px var(--space-5);
  background: var(--color-text-primary);
  color: var(--color-surface);
  font-size: var(--font-xs);
}

.toast-tail {
  font-family: monospace;
  opacity: 0.7;
}

.card-filter {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-5);
  border-bottom: 1px solid var(--color-border);
  overflow-x: auto;
}

.preview-area {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-6);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-7);
}

.preview-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.preview-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-xs);
  padding: 0 2px;
  flex-wrap: wrap;
}

.warn-text {
  color: var(--color-warning);
  font-weight: 500;
}

.card-frame {
  position: relative;
  background: #fff;
  border: 1px solid var(--color-border-strong);
  box-shadow: var(--shadow-md);
  overflow: hidden;
}

.panel-inspector {
  background: var(--color-surface);
}

.inspector-tabs {
  margin: var(--space-3);
  flex: 0 0 auto;
}

.inspector-body {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-4) var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.inspector-foot {
  justify-content: stretch;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  font-size: var(--font-sm);
}

.section-strong {
  font-size: var(--font-sm);
  font-weight: 600;
}

.divider {
  height: 1px;
  background: var(--color-border);
  margin: var(--space-2) 0;
}

.template-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.template-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 8px;
}

.template-item:hover {
  border-color: var(--color-border-strong);
}

.template-item.is-selected {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}

.template-pick {
  flex: 1;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
  background: none;
  border: none;
  padding: 0;
  text-align: left;
  cursor: pointer;
}

.swatch {
  position: relative;
  width: 40px;
  height: 48px;
  border: 1px solid;
  border-radius: 4px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 700;
  overflow: hidden;
}

.swatch-dot {
  position: absolute;
  bottom: 4px;
  right: 4px;
  width: 8px;
  height: 8px;
  border-radius: 1px;
}

.template-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.template-name {
  font-size: var(--font-xs);
  font-weight: 700;
  color: var(--color-text-primary);
  display: flex;
  gap: 4px;
  align-items: baseline;
}

.template-name em {
  font-style: normal;
  font-weight: 400;
  font-size: 10px;
  color: var(--color-accent);
}

.template-sub {
  font-size: 11px;
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.icon-link {
  background: none;
  border: none;
  font-size: 11px;
  color: var(--color-text-muted);
  cursor: pointer;
  flex-shrink: 0;
}

.icon-link.danger:hover {
  color: var(--color-danger);
}

.check-mark {
  color: var(--color-accent);
  font-weight: 700;
  flex-shrink: 0;
}

.inline-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.field {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.inline-color {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 6px 8px;
}

.color-input {
  width: 24px;
  height: 24px;
  padding: 0;
  border: 1px solid var(--color-border-strong);
  border-radius: 4px;
  background: none;
  cursor: pointer;
}

.swatch-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2);
}

.swatch-grid .chip {
  justify-content: center;
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 1px solid var(--color-border-strong);
}

.option-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2);
}

.option-grid.cols-3 {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.option-grid .chip {
  justify-content: flex-start;
}

.slider-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
}

.slider-row input[type='range'] {
  width: 100%;
  accent-color: var(--color-accent);
}

.slider-row > span:first-child {
  flex: 1;
}

.checkbox {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
}

.error-text {
  margin: 0;
  font-size: var(--font-xs);
  color: var(--color-danger);
}

@media (max-width: 1180px) {
  .studio-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .panel-editor,
  .panel-preview {
    border-right: none;
    border-bottom: 1px solid var(--color-border);
  }

  .inspector-body {
    max-height: 520px;
  }
}
</style>
