<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { NButton, NInput, NModal } from 'naive-ui'
import { api, apiUrl } from '../../api/client'

/**
 * 角色参考图选择弹窗：大图网格浏览素材图库，点选即回传 assetId。
 * 只展示图片素材；已在别处选过的 assetId 传 disabledIds 置灰，避免同一张图重复占用多个角度位。
 */
const props = defineProps({
  show: Boolean,
  width: { type: String, default: '960px' },
  disabledIds: { type: Array, default: () => [] },
  selectedId: { type: String, default: '' },
})
const emit = defineEmits(['update:show', 'pick'])

const items = ref([])
const keyword = ref('')
const loading = ref(false)
const error = ref('')
const brokenIds = ref([])

const disabledSet = computed(() => new Set(props.disabledIds || []))

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
  return disabledSet.value.has(item.id) && item.id !== props.selectedId
}

function isSelected(item) {
  return item.id === props.selectedId
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

watch(() => props.show, (open) => {
  if (open) {
    keyword.value = ''
    load()
  }
})
onMounted(() => { if (props.show) load() })
</script>

<template>
  <n-modal
    :show="show"
    preset="card"
    title="选择参考图"
    class="asset-picker-modal"
    :style="{ width: `min(${width}, 96vw)` }"
    :mask-closable="false"
    @update:show="emit('update:show', $event)"
  >
    <div class="picker-bar">
      <n-input
        v-model:value="keyword"
        placeholder="按文件名搜索，回车确认"
        clearable
        @keyup.enter="load"
        @clear="load"
      />
      <n-button secondary @click="load">搜索</n-button>
    </div>
    <p class="picker-hint">
      点任意图片即选中，大图预览更清楚；没有需要的？先到「素材图库」上传，再回到角色里重新打开这里挑选。
    </p>
    <p v-if="error" class="picker-error">{{ error }}</p>
    <div v-else-if="loading" class="picker-loading">正在读取素材…</div>
    <div v-else-if="!items.length" class="picker-empty">
      <span class="picker-empty-icon" aria-hidden="true">🖼</span>
      <span class="picker-empty-title">{{ keyword.trim() ? '没有匹配的图片' : '素材图库里还没有图片' }}</span>
      <span class="picker-empty-sub">{{ keyword.trim() ? '换个关键词再试试。' : '去「素材」页上传一张，再回来挑选。' }}</span>
    </div>
    <div v-else class="picker-grid">
      <button
        v-for="item in items"
        :key="item.id"
        type="button"
        class="picker-item"
        :class="{ 'is-disabled': isDisabled(item) || isBroken(item), 'is-selected': isSelected(item) }"
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
          <span v-if="isSelected(item)" class="picker-badge">当前</span>
          <span v-else-if="isDisabled(item)" class="picker-badge picker-badge-muted">已选</span>
          <span v-if="item.width && item.height" class="picker-size">{{ item.width }}×{{ item.height }}</span>
        </span>
        <span class="picker-name">{{ item.originalName }}</span>
      </button>
    </div>
  </n-modal>
</template>

<style scoped>
.asset-picker-modal :deep(.n-card__content) { padding-top: 12px; }
.picker-bar { display: flex; gap: 8px; }
.picker-hint { margin: 8px 0 12px; color: var(--paper-ink-muted); font-size: 12px; }
.picker-error { color: var(--paper-danger); font-size: 13px; }
.picker-loading { padding: 40px 0; text-align: center; color: var(--paper-ink-muted); font-size: 13px; }
.picker-empty { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 48px 0; color: var(--paper-ink-muted); }
.picker-empty-icon { font-size: 28px; opacity: 0.6; }
.picker-empty-title { color: var(--paper-ink-soft); font-size: 14px; font-weight: 600; }
.picker-empty-sub { font-size: 12px; }
.picker-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(176px, 1fr));
  gap: 12px;
  max-height: min(62vh, 640px);
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
  background: var(--paper-surface);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.15s ease, background 0.15s ease, box-shadow 0.15s ease;
}
.picker-item:hover:not(:disabled) { border-color: var(--paper-accent); background: var(--paper-accent-tint); box-shadow: 0 2px 8px color-mix(in srgb, var(--paper-accent) 12%, transparent); }
.picker-item:focus-visible { outline: 2px solid var(--paper-accent); outline-offset: 2px; }
.picker-item.is-selected { border-color: var(--paper-accent); background: var(--paper-accent-soft); box-shadow: 0 0 0 2px color-mix(in srgb, var(--paper-accent) 24%, transparent); }
.picker-item.is-disabled { cursor: not-allowed; opacity: 0.45; }
.picker-preview {
  position: relative;
  display: grid;
  place-items: center;
  width: 100%;
  aspect-ratio: 3 / 4;
  overflow: hidden;
  border-radius: 7px;
  background: var(--paper-thumb);
  color: var(--paper-ink-muted);
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
.picker-name { overflow: hidden; font-size: 12px; color: var(--paper-ink-soft); white-space: nowrap; text-overflow: ellipsis; }
</style>