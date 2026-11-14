<script setup>
import { computed, ref, watch } from 'vue'
import { NButton, NInput, NSelect } from 'naive-ui'
import { api } from '../../api/client'

const props = defineProps({ project: { type: Object, default: null } })
const emit = defineEmits(['save'])
const category = ref('characters')
const name = ref('')
const description = ref('')
const assetId = ref(null)
const library = ref([])
const error = ref('')
const categories = [
  { key: 'characters', label: '角色' },
  { key: 'locations', label: '地点' },
  { key: 'styles', label: '风格' },
]
const items = computed(() => props.project?.assets?.[category.value] || [])
const options = computed(() => library.value.map((item) => ({ label: item.originalName || item.id, value: item.id })))

watch(() => props.project?.id, async (id) => {
  if (!id || library.value.length) return
  try { library.value = await api.get('/assets?limit=100') } catch { library.value = [] }
}, { immediate: true })

function newId() {
  return Math.random().toString(16).slice(2, 10) + Math.random().toString(16).slice(2, 10)
}

function save(next) {
  emit('save', {
    characters: props.project?.assets?.characters || [],
    locations: props.project?.assets?.locations || [],
    styles: props.project?.assets?.styles || [],
    [category.value]: next,
  })
}

function addItem() {
  error.value = ''
  if (!name.value.trim() || !(description.value.trim() || assetId.value)) {
    error.value = '名字必填，参考图或文字描述至少有一项'
    return
  }
  save([...items.value, {
    id: newId(), name: name.value.trim(), description: description.value.trim(), assetId: assetId.value || null,
  }])
  name.value = ''
  description.value = ''
  assetId.value = null
}

function removeItem(id) {
  save(items.value.filter((item) => item.id !== id))
}
</script>

<template>
  <section class="asset-board">
    <header>
      <div><span>项目视觉资产</span><strong>角色参考图只出现在点到名字的镜头里；没点到名字时才用本镜勾选</strong></div>
    </header>
    <div class="tabs">
      <button v-for="item in categories" :key="item.key" type="button" :class="{ active: category === item.key }" @click="category = item.key">{{ item.label }}</button>
    </div>
    <ul>
      <li v-for="item in items" :key="item.id"><span>{{ item.name }}</span><small>{{ item.description || '已绑参考图' }}</small><button type="button" @click="removeItem(item.id)">移除</button></li>
      <li v-if="!items.length" class="empty">这一类还没有登记</li>
    </ul>
    <div class="composer">
      <n-input v-model:value="name" size="small" placeholder="名字" />
      <n-input v-model:value="description" size="small" placeholder="文字描述，可留空" />
      <n-select v-model:value="assetId" size="small" clearable :options="options" placeholder="从素材库选参考图" />
      <n-button size="small" secondary @click="addItem">登记</n-button>
    </div>
    <p v-if="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.asset-board { border-top: 1px solid #d9d6cf; background: #f7f4ee; padding: 16px; }
header strong, header span { display: block; }
header span { color: #8c8175; font-size: 11px; }
header strong { margin-top: 2px; color: #26231f; }
.tabs { display: flex; gap: 6px; margin: 12px 0; }
.tabs button { border: 1px solid #d4cec4; background: transparent; padding: 4px 10px; cursor: pointer; }
.tabs button.active { background: #315e68; color: white; border-color: #315e68; }
ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
li { display: grid; grid-template-columns: 80px 1fr auto; gap: 8px; align-items: center; font-size: 12px; }
li small { color: #81786e; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
li button, .empty { color: #8d5a52; background: transparent; border: 0; cursor: pointer; }
.empty { display: block; color: #8c8175; }
.composer { display: grid; grid-template-columns: 1fr 1.4fr 1.4fr auto; gap: 6px; margin-top: 12px; }
p { color: #9b3e37; font-size: 12px; }
@media (max-width: 900px) { .composer, li { grid-template-columns: 1fr; } }
</style>
