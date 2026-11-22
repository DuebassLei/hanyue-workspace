<script setup>
/**
 * 成稿台的素材选图弹窗：大图网格浏览素材图库，点选即回传素材。
 *
 * 为什么要弹窗而不是在右栏塞一个缩略图列表：右栏只有 300 多 px 宽，
 * 一列小图根本认不出哪张是哪张，选图变成猜。弹窗能铺开成多列大图，
 * 认图、对比、确认都在一处。
 */
import { computed, ref, watch } from 'vue'
import { NButton, NInput, NModal } from 'naive-ui'
import { api, apiUrl } from '../../api/client'

const props = defineProps({
  show: Boolean,
  title: { type: String, default: '从素材图库选图' },
  hint: { type: String, default: '' },
  // 当前已经用上的素材 id：置灰，避免同一张图在同一篇里重复出现
  usedIds: { type: Array, default: () => [] },
  selectedId: { type: String, default: '' },
})
const emit = defineEmits(['update:show', 'pick'])

const items = ref([])
const keyword = ref('')
const loading = ref(false)
const error = ref('')
const brokenIds = ref([])

const usedSet = computed(() => new Set(props.usedIds || []))

async function load() {
  loading.value = true
  error.value = ''
  brokenIds.value = []
  try {
    const params = new URLSearchParams({ limit: '200', format: 'image' })
    if (keyword.value.trim()) params.set('q', keyword.value.trim())
    const rows = await api.get(`/assets?${params}`)
    items.value = (rows || []).filter((item) => String(item.mimeType || '').startsWith('image/'))
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function imageUrl(item) {
  return item?.url ? apiUrl(item.url) : ''
}

function isDisabled(item) {
  return usedSet.value.has(item.id) && item.id !== props.selectedId
}

function isBroken(item) {
  return brokenIds.value.includes(item.id)
}

function markBroken(item) {
  if (!brokenIds.value.includes(item.id)) brokenIds.value.push(item.id)
}

function pick(item) {
  if (isDisabled(item) || isBroken(item)) return
  emit('pick', item)
  emit('update:show', false)
}

watch(
  () => props.show,
  (open) => {
    if (open) {
      keyword.value = ''
      load()
    }
  },
)
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="title"
    class="paper-asset-picker"
    style="width: min(1080px, 96vw)"
    :mask-closable="false"
    @update:show="emit('update:show', $event)"
  >
    <div class="picker-bar">
      <NInput
        v-model:value="keyword"
        placeholder="按文件名或提示词搜索，回车确认"
        clearable
        @keyup.enter="load"
        @clear="load"
      />
      <NButton secondary @click="load">搜索</NButton>
    </div>
    <p class="picker-hint">
      {{ hint || '点任意图片即选中；素材图库里没有合适的，先去「素材图库」上传一张再回来挑。' }}
    </p>
    <p v-if="error" class="picker-error">{{ error }}</p>
    <div v-else-if="loading" class="picker-loading">正在读取素材…</div>
    <div v-else-if="!items.length" class="picker-empty">
      <span class="picker-empty-icon" aria-hidden="true">🖼</span>
      <span class="picker-empty-title">{{ keyword.trim() ? '没有匹配的图片' : '素材图库里还没有图片' }}</span>
      <span class="picker-empty-sub">{{ keyword.trim() ? '换个关键词再试试。' : '去「素材图库」上传一张，再回来挑选。' }}</span>
    </div>
    <div v-else class="picker-grid">
      <button
        v-for="item in items"
        :key="item.id"
        type="button"
        class="picker-item"
        :class="{ 'is-disabled': isDisabled(item) || isBroken(item), 'is-selected': item.id === props.selectedId }"
        :disabled="isDisabled(item) || isBroken(item)"
        :title="item.originalName"
        @click="pick(item)"
      >
        <span class="picker-preview">
          <img
            v-if="imageUrl(item) && !isBroken(item)"
            :src="imageUrl(item)"
            :alt="item.originalName"
            loading="lazy"
            @error="markBroken(item)"
          />
          <span v-else-if="isBroken(item)" class="preview-fallback">图片加载失败</span>
          <span v-else class="preview-fallback">无预览</span>
          <span v-if="item.id === props.selectedId" class="picker-badge">当前</span>
          <span v-else-if="isDisabled(item)" class="picker-badge picker-badge-muted">已用</span>
          <span v-if="item.width && item.height" class="picker-size">{{ item.width }}×{{ item.height }}</span>
        </span>
        <span class="picker-name">{{ item.originalName }}</span>
      </button>
    </div>
  </NModal>
</template>

<style scoped>
.paper-asset-picker :deep(.n-card__content) { padding-top: 12px; }
.picker-bar { display: flex; gap: 8px; }
.picker-hint { margin: 8px 0 12px; color: var(--color-text-tertiary); font-size: 12px; line-height: 1.6; }
.picker-error { color: var(--paper-danger); font-size: 13px; }
.picker-loading { padding: 40px 0; text-align: center; color: var(--color-text-tertiary); font-size: 13px; }
.picker-empty { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 48px 0; color: var(--color-text-tertiary); }
.picker-empty-icon { font-size: 28px; opacity: .6; }
.picker-empty-title { color: var(--color-text-secondary); font-size: 14px; font-weight: 600; }
.picker-empty-sub { font-size: 12px; }
.picker-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(184px, 1fr));
  gap: 12px;
  max-height: min(64vh, 660px);
  overflow: auto;
  padding: 2px;
}
.picker-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border: 1px solid var(--paper-line);
  border-radius: 10px;
  background: var(--color-surface);
  cursor: pointer;
  text-align: left;
  transition: border-color .15s ease, background .15s ease, box-shadow .15s ease;
}
.picker-item:hover:not(:disabled) {
  border-color: var(--paper-accent);
  background: var(--paper-accent-tint);
  box-shadow: var(--shadow-md);
}
.picker-item:focus-visible { outline: 2px solid var(--paper-accent); outline-offset: 2px; }
.picker-item.is-selected {
  border-color: var(--paper-accent);
  background: var(--paper-accent-soft);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--paper-accent) 22%, transparent);
}
.picker-item.is-disabled { cursor: not-allowed; opacity: .45; }
.picker-preview {
  position: relative;
  display: grid;
  place-items: center;
  width: 100%;
  aspect-ratio: 4 / 3;
  overflow: hidden;
  border-radius: 7px;
  background: var(--color-surface-muted);
  color: var(--color-text-tertiary);
  font-size: 12px;
}
.picker-preview img { width: 100%; height: 100%; object-fit: cover; }
.preview-fallback { padding: 8px; text-align: center; }
.picker-badge {
  position: absolute;
  top: 6px;
  left: 6px;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--paper-accent);
  color: var(--paper-on-accent);
  font-size: 11px;
  line-height: 18px;
}
.picker-badge-muted { background: var(--paper-scrim); }
.picker-size {
  position: absolute;
  right: 6px;
  bottom: 6px;
  padding: 0 6px;
  border-radius: 4px;
  background: var(--paper-scrim);
  color: var(--paper-on-scrim);
  font-size: 10px;
  line-height: 16px;
}
.picker-name { overflow: hidden; font-size: 12px; color: var(--color-text-secondary); white-space: nowrap; text-overflow: ellipsis; }
</style>
