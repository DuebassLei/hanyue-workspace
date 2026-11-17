<script setup>
import { onMounted, ref, watch } from 'vue'
import { NButton, NInput, NModal } from 'naive-ui'
import { api, apiUrl } from '../../api/client'

/** 素材选择弹窗：按类型（image/audio）浏览素材图库，选中后回传 AssetOut。 */
const props = defineProps({ show: Boolean, kind: { type: String, default: 'image' } })
const emit = defineEmits(['update:show', 'pick'])

const items = ref([])
const keyword = ref('')
const loading = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({ limit: '60' })
    if (props.kind === 'image') params.set('format', 'image')
    else if (props.kind === 'audio') params.set('format', 'audio')
    if (keyword.value.trim()) params.set('q', keyword.value.trim())
    items.value = await api.get(`/assets?${params}`)
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function pick(item) {
  emit('pick', item)
  emit('update:show', false)
}

function imageUrl(item) {
  return item?.url ? apiUrl(item.url) : ''
}

watch(() => props.show, (open) => { if (open) load() })
onMounted(() => { if (props.show) load() })
</script>

<template>
  <n-modal
    :show="show"
    preset="card"
    :title="kind === 'audio' ? '选择背景音乐' : '选择图片素材'"
    style="width:min(720px,92vw)"
    @update:show="(v) => emit('update:show', v)"
  >
    <div class="picker-bar">
      <n-input v-model:value="keyword" size="small" placeholder="搜索素材名称" clearable @keyup.enter="load" />
      <n-button size="small" secondary @click="load">搜索</n-button>
    </div>
    <p v-if="error" class="picker-error">{{ error }}</p>
    <div v-else-if="loading" class="picker-loading">正在读取素材…</div>
    <div v-else-if="!items.length" class="picker-loading">没有匹配的素材</div>
    <div v-else class="picker-grid">
      <button v-for="item in items" :key="item.id" type="button" class="picker-item" @click="pick(item)">
        <div class="picker-preview">
          <img
            v-if="item.mimeType?.startsWith('image/') && imageUrl(item)"
            :src="imageUrl(item)"
            :alt="item.originalName"
            loading="lazy"
            @error="$event.target.style.display = 'none'"
          />
          <div v-else-if="item.mimeType?.startsWith('image/')" class="preview-fallback">图片不可预览</div>
          <span v-else class="picker-audio">♪</span>
        </div>
        <span class="picker-name" :title="item.originalName">{{ item.originalName }}</span>
      </button>
    </div>
  </n-modal>
</template>

<style scoped>
.picker-bar { display:flex; gap:8px; margin-bottom:12px; }
.picker-error { color:var(--paper-danger); font-size:13px; }
.picker-loading { padding:26px 0; text-align:center; color:var(--paper-ink-muted); font-size:13px; }
.picker-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(120px,1fr)); gap:10px; max-height:52vh; overflow:auto; }
.picker-item { display:flex; flex-direction:column; gap:5px; padding:6px; border:1px solid var(--paper-line); border-radius:8px; background:var(--paper-surface); cursor:pointer; text-align:left; }
.picker-item:hover { border-color:var(--paper-accent); background:var(--paper-accent-tint); }
.picker-preview { display:grid; place-items:center; width:100%; aspect-ratio:4/3; overflow:hidden; border-radius:5px; background:var(--paper-thumb); color:var(--paper-ink-muted); font-size:11px; }
.picker-item img { width:100%; height:100%; object-fit:cover; }
.preview-fallback { padding:8px; text-align:center; }
.picker-audio { color:var(--paper-accent); font-size:26px; }
.picker-name { overflow:hidden; font-size:11px; color:var(--paper-ink-soft); white-space:nowrap; text-overflow:ellipsis; }
</style>
