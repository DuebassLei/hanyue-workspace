<script setup>
import { computed, ref } from 'vue'
import { NAlert, NButton, NInput, NSlider } from 'naive-ui'
import { ideateOutline } from './ideate.js'

const props = defineProps({
  styles: { type: Array, default: () => [] },
  trackDefaults: { type: Object, default: () => ({}) },
  narratives: { type: Array, default: () => [] },
  // 编辑已有集合时回填：{ theme, styleId, narrative, images }
  initial: { type: Object, default: null },
  busy: { type: Boolean, default: false },
})

const emit = defineEmits(['submit'])

// 风格卡色块：按风格 id 固定一个代表色，纯装饰用
const STYLE_COLOR = {
  humanist_photo: '#8a6d4b',
  minimal_tech: '#2f6fb3',
  flat_illustration: '#e0803a',
  handdraw_note: '#c9576b',
  info_card: '#3f8f7a',
  retro_nostalgia: '#a4762f',
}

function styleColor(id) {
  return STYLE_COLOR[id] || 'var(--color-accent)'
}

const theme = ref(props.initial?.theme || '')
// initial.styleId：编辑已有集合时用集合自己的风格
// initial.trackStyleId：新建/从热点预填时，由容器按 trackDefaults 命中的默认风格
const styleId = ref(
  props.initial?.styleId
  || props.initial?.trackStyleId
  || props.styles[0]?.id
  || '',
)
// 新建默认 6 张；编辑已有集合时张数跟分镜行数走（「再补一张」在 Step2 里加行）
const initialCount = props.initial?.styleId ? props.initial?.images?.length : 0
const count = ref(Math.min(9, Math.max(2, initialCount || 6)))
const narrative = ref(props.initial?.narrative || 'parallel')
const ideating = ref(false)
const error = ref('')

const selectedStyle = computed(() => props.styles.find((item) => item.id === styleId.value) || null)
const recommendedIds = computed(() => new Set(Object.values(props.trackDefaults)))

/** AI 构思：SSE 出分镜后带着结果进 Step2；失败只提示，不挡手动路线。 */
async function ideate() {
  if (!canSubmit.value || ideating.value) return
  error.value = ''
  ideating.value = true
  try {
    const items = await ideateOutline({
      theme: theme.value.trim(),
      styleName: selectedStyle.value?.name || '',
      count: Number(count.value),
    })
    emit('submit', {
      mode: 'ideate',
      payload: buildPayload(),
      items,
    })
  } catch (err) {
    error.value = err.message
  } finally {
    ideating.value = false
  }
}

function buildPayload() {
  return {
    theme: theme.value.trim(),
    title: theme.value.trim().slice(0, 40),
    styleId: styleId.value,
    narrative: narrative.value,
  }
}

const canSubmit = computed(
  () => theme.value.trim().length > 0 && Boolean(styleId.value) && !props.busy,
)

function manualNext() {
  if (!canSubmit.value) return
  emit('submit', { mode: 'manual', payload: buildPayload() })
}
</script>

<template>
  <div class="ideate-step stack">
    <label class="field">
      <span class="field-label">主题</span>
      <n-input
        v-model:value="theme"
        type="textarea"
        placeholder="这组贴图讲一件什么事？例如：年轻人开始逛菜市场了"
        :autosize="{ minRows: 2, maxRows: 5 }"
      />
      <span class="field-hint">一句话就够。AI 会围绕它拆出 {{ count }} 张画面。</span>
    </label>

    <div class="field">
      <span class="field-label">画风</span>
      <div class="style-grid">
        <button
          v-for="item in styles"
          :key="item.id"
          type="button"
          class="style-card"
          :class="{ 'is-active': styleId === item.id }"
          @click="styleId = item.id"
        >
          <span v-if="recommendedIds.has(item.id)" class="style-badge">按赛道推荐</span>
          <span class="style-swatch" :style="{ background: styleColor(item.id) }" aria-hidden="true" />
          <span class="style-name">{{ item.name }}</span>
          <span class="style-desc">{{ item.desc }}</span>
          <span class="style-tags">
            <span v-for="tag in item.tags || []" :key="tag" class="tag">{{ tag }}</span>
          </span>
        </button>
      </div>
    </div>

    <div class="ideate-row">
      <label class="field ideate-count">
        <span class="field-label">张数</span>
        <n-slider v-model:value="count" :min="2" :max="9" :step="1" :marks="{ 2: '2', 6: '6', 9: '9' }" />
        <span class="field-hint">当前 {{ count }} 张 · 九宫格最多 9 张</span>
      </label>
    </div>

    <div class="field">
      <span class="field-label">叙事线</span>
      <div class="narrative-list">
        <label
          v-for="item in narratives"
          :key="item.id"
          class="narrative-item"
          :class="{ 'is-active': narrative === item.id }"
        >
          <input v-model="narrative" type="radio" name="sticker-narrative" :value="item.id" />
          <span class="narrative-main">
            <b>{{ item.name }}</b>
            <span>{{ item.desc }}</span>
          </span>
        </label>
      </div>
    </div>

    <n-alert v-if="error" type="warning" :bordered="false">{{ error }}</n-alert>

    <div class="row ideate-foot">
      <span class="field-hint">「AI 帮我构思」直接出分镜草稿；也可以先手动进下一步自己排。</span>
      <div class="spacer" />
      <n-button secondary :disabled="!canSubmit" @click="manualNext">下一步，自己排分镜</n-button>
      <n-button type="primary" :loading="ideating" :disabled="!canSubmit" @click="ideate">
        {{ ideating ? 'AI 正在拆画面…' : 'AI 帮我构思' }}
      </n-button>
    </div>
  </div>
</template>

<style scoped>
.style-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: var(--space-2);
}
.style-card {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 5px;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.style-card:hover {
  border-color: var(--color-accent);
}
.style-card.is-active {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 1px var(--color-accent) inset;
}
.style-badge {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 10.5px;
  font-weight: 600;
  color: var(--color-on-accent);
  background: var(--color-accent);
}
.style-swatch {
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
}
.style-name {
  font-size: var(--font-body);
  font-weight: 600;
}
.style-desc {
  font-size: var(--font-xs);
  line-height: 1.55;
  color: var(--color-text-secondary);
}
.style-tags {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.ideate-row {
  display: flex;
  gap: var(--space-4);
}
.ideate-count {
  width: min(320px, 100%);
  padding-bottom: var(--space-4);
}
.narrative-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.narrative-item {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
}
.narrative-item:hover {
  border-color: var(--color-accent);
}
.narrative-item.is-active {
  border-color: var(--color-accent);
  background: color-mix(in srgb, var(--color-accent) 6%, transparent);
}
.narrative-item input {
  margin-top: 3px;
  accent-color: var(--color-accent);
}
.narrative-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: var(--font-sm);
}
.narrative-main span {
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
}
.ideate-foot {
  padding-top: var(--space-2);
  border-top: 1px dashed var(--color-border);
}
</style>
