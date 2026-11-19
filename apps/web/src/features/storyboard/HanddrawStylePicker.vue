<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NButton, NInput } from 'naive-ui'
import { fetchBlobUrl } from '../../api/client'

const props = defineProps({
  styles: { type: Array, default: () => [] },
  groups: { type: Array, default: () => [] },
  modelValue: { type: String, default: '' },
  loading: Boolean,
  disabled: Boolean,
})
const emit = defineEmits(['update:modelValue'])
const query = ref('')
const activeGroup = ref('')
const thumbnails = ref(new Map())
const failedThumbs = ref(new Set())
let requestVersion = 0

const filteredStyles = computed(() => {
  const text = query.value.trim().toLocaleLowerCase()
  return props.styles.filter((style) => {
    if (activeGroup.value && style.groupKey !== activeGroup.value) return false
    if (!text) return true
    return [style.number, style.generationName, style.referenceAuthor, style.groupName]
      .some((value) => String(value || '').toLocaleLowerCase().includes(text))
  })
})
const groupedStyles = computed(() => props.groups
  .map((group) => ({ ...group, items: filteredStyles.value.filter((style) => style.groupKey === group.key) }))
  .filter((group) => group.items.length))
const selectedStyle = computed(() => props.styles.find((style) => style.number === props.modelValue) || null)

watch([filteredStyles, selectedStyle], async ([styles, selected]) => {
  const version = ++requestVersion
  // 先加载当前结果前 24 张缩略图，避免初次打开为 331 档风格并发拉取全部图片。
  const visible = [...styles.slice(0, 24), ...(selected ? [selected] : [])]
    .filter((style, index, all) => style.referenceAvailable && all.findIndex((item) => item.number === style.number) === index)
  await Promise.all(visible.map(async (style) => {
    if (thumbnails.value.has(style.number) || failedThumbs.value.has(style.number)) return
    try {
      const url = await fetchBlobUrl(`/handdraw/styles/${style.number}/reference`)
      if (version !== requestVersion) {
        URL.revokeObjectURL(url)
        return
      }
      const next = new Map(thumbnails.value)
      next.set(style.number, url)
      thumbnails.value = next
    } catch {
      failedThumbs.value = new Set([...failedThumbs.value, style.number])
    }
  }))
}, { immediate: true })

function thumbnail(style) {
  return thumbnails.value.get(style.number) || ''
}
function choose(style) {
  if (!props.disabled) emit('update:modelValue', style.number)
}
function clear() {
  if (!props.disabled) emit('update:modelValue', '')
}
onBeforeUnmount(() => {
  requestVersion += 1
  for (const url of thumbnails.value.values()) URL.revokeObjectURL(url)
})
</script>

<template>
  <section class="picker" aria-label="手绘统一画风">
    <div class="picker-head">
      <div class="picker-intro">
        <span class="picker-mark" aria-hidden="true">✎</span>
        <div><h2>统一画风</h2><p>为新生成的镜头固定一套视觉语言</p></div>
      </div>
      <NButton v-if="props.modelValue" size="small" quaternary :disabled="props.disabled" @click="clear">清除选择</NButton>
    </div>
    <div class="picker-tools">
      <NInput v-model:value="query" clearable size="small" placeholder="搜索风格名、作者或编号" aria-label="搜索手绘风格" />
      <div class="group-tabs" role="group" aria-label="风格分类">
        <button type="button" :class="{ active: !activeGroup }" :disabled="props.disabled" @click="activeGroup = ''">全部 <span>{{ props.styles.length }}</span></button>
        <button v-for="group in props.groups" :key="group.key" type="button" :class="{ active: activeGroup === group.key }" :disabled="props.disabled" @click="activeGroup = activeGroup === group.key ? '' : group.key">{{ group.name }} <span>{{ group.count }}</span></button>
      </div>
    </div>
    <div v-if="selectedStyle" class="selected-note" role="status">
      <img v-if="thumbnail(selectedStyle)" :src="thumbnail(selectedStyle)" :alt="`${selectedStyle.generationName} 风格参考`" />
      <span class="selected-copy"><strong>{{ selectedStyle.generationName }}</strong><small>{{ selectedStyle.referenceAuthor }} · {{ selectedStyle.number }}</small></span>
      <span class="lock-badge" :title="selectedStyle.needReference ? '使用内置参考图锁定画风' : '使用模型可识别的风格名称或特征词'">{{ selectedStyle.needReference ? '参考图锁定' : '文字锁定' }}</span>
    </div>
    <div v-if="props.loading" class="picker-state" role="status">正在读取手绘风格库…</div>
    <div v-else-if="!groupedStyles.length" class="picker-state">没有匹配的风格，试试其他关键词。</div>
    <div v-else class="style-groups">
      <section v-for="group in groupedStyles" :key="group.key" class="style-group">
        <h3><span>{{ group.name }}</span><small>{{ group.items.length }} 种风格</small></h3>
        <div class="style-grid">
          <button v-for="style in group.items" :key="style.number" type="button" class="style-option" :class="{ selected: props.modelValue === style.number }" :disabled="props.disabled" :aria-pressed="props.modelValue === style.number" @click="choose(style)">
            <span class="style-image">
              <img v-if="thumbnail(style)" :src="thumbnail(style)" :alt="`${style.generationName} 参考图`" loading="lazy" />
              <span v-else class="image-placeholder" aria-hidden="true">{{ style.groupKey }}</span>
              <span class="image-number">{{ style.number }}</span>
              <span v-if="props.modelValue === style.number" class="selected-mark" aria-label="已选">✓</span>
            </span>
            <span class="style-copy"><strong>{{ style.generationName }}</strong><small>{{ style.referenceAuthor }}</small></span>
            <span class="lock-badge">{{ style.needReference ? '参考图' : '文字' }}</span>
          </button>
        </div>
      </section>
    </div>
    <p class="picker-foot">未选择时沿用原有出图方式。更换画风只影响之后新生成的镜头。</p>
  </section>
</template>

<style scoped>
.picker { padding:14px 16px 12px; border-bottom:1px solid var(--paper-line-strong); background:var(--paper-header); }
.picker-head { display:flex; justify-content:space-between; align-items:center; gap:12px; }
.picker-intro { display:flex; align-items:center; gap:11px; }
.picker-mark { display:grid; place-items:center; width:34px; height:34px; border:1px solid var(--paper-accent-line); color:var(--paper-accent); background:var(--paper-accent-tint); font-size:19px; }
.picker-intro h2 { margin:0; color:var(--paper-ink); font-size:15px; font-weight:650; }
.picker-intro p { margin:3px 0 0; color:var(--paper-ink-muted); font-size:11px; }
.picker-tools { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-top:12px; }
.picker-tools :deep(.n-input) { max-width:300px; }
.group-tabs { display:flex; align-items:center; flex-wrap:wrap; gap:5px; }
.group-tabs button { padding:5px 8px; border:1px solid transparent; background:transparent; color:var(--paper-ink-soft); font:inherit; font-size:11px; cursor:pointer; }
.group-tabs button span { margin-left:3px; color:var(--paper-ink-muted); font-size:10px; }
.group-tabs button.active { border-color:var(--paper-accent-line); background:var(--paper-accent-tint); color:var(--paper-accent); }
.group-tabs button:focus-visible,.style-option:focus-visible { outline:2px solid var(--paper-accent); outline-offset:2px; }
.selected-note { display:flex; align-items:center; gap:10px; margin-top:11px; padding:7px 9px; border-left:3px solid var(--paper-accent); background:var(--paper-surface); }
.selected-note img { width:44px; height:34px; object-fit:cover; }
.selected-copy { display:flex; min-width:0; flex:1; flex-direction:column; gap:3px; }
.selected-copy strong,.style-copy strong { overflow:hidden; color:var(--paper-ink); font-size:11px; font-weight:600; text-overflow:ellipsis; white-space:nowrap; }
.selected-copy small,.style-copy small { overflow:hidden; color:var(--paper-ink-muted); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.lock-badge { flex:none; padding:3px 6px; border:1px solid var(--paper-line); color:var(--paper-ink-muted); font-size:9px; white-space:nowrap; }
.style-groups { display:flex; max-height:340px; flex-direction:column; gap:12px; margin-top:12px; overflow:auto; }
.style-group h3 { display:flex; align-items:baseline; gap:8px; margin:0 0 7px; color:var(--paper-ink-soft); font-size:11px; font-weight:650; }
.style-group h3 small { color:var(--paper-ink-muted); font-size:10px; font-weight:400; }
.style-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(178px,1fr)); gap:6px; }
.style-option { display:grid; grid-template-columns:58px minmax(0,1fr); grid-template-rows:44px auto; align-items:center; column-gap:8px; padding:5px; border:1px solid var(--paper-line); background:var(--paper-surface); text-align:left; cursor:pointer; }
.style-option:hover { border-color:var(--paper-line-strong); }
.style-option.selected { border-color:var(--paper-accent); box-shadow:inset 2px 0 var(--paper-accent); }
.style-option:disabled { cursor:default; opacity:.65; }
.style-image { position:relative; grid-row:1 / 3; width:58px; height:50px; overflow:hidden; background:var(--paper-sunken); }
.style-image img { width:100%; height:100%; object-fit:cover; }
.image-placeholder { display:grid; width:100%; height:100%; place-items:center; color:var(--paper-ink-muted); font-size:11px; }
.image-number { position:absolute; left:3px; bottom:3px; padding:1px 3px; background:var(--paper-scrim-strong); color:var(--paper-on-scrim); font-size:8px; }
.selected-mark { position:absolute; right:3px; top:3px; display:grid; width:16px; height:16px; place-items:center; background:var(--paper-accent); color:var(--paper-on-accent); font-size:10px; }
.style-copy { display:flex; min-width:0; flex-direction:column; gap:3px; }
.style-option .lock-badge { grid-column:2; justify-self:start; padding:1px 4px; font-size:8px; }
.picker-state { padding:20px 8px; color:var(--paper-ink-muted); font-size:12px; text-align:center; }
.picker-foot { margin:9px 0 0; color:var(--paper-ink-muted); font-size:10px; }
@media(max-width:720px) { .picker-tools { align-items:stretch; flex-direction:column; }.picker-tools :deep(.n-input) { max-width:none; }.group-tabs { flex-wrap:nowrap; overflow:auto; }.group-tabs button { flex:none; }.style-groups { max-height:300px; }.style-grid { grid-template-columns:repeat(auto-fill,minmax(154px,1fr)); }.picker-intro p { max-width:220px; } }
</style>
