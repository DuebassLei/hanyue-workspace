<script setup>
import { computed, ref, watch } from 'vue'
import { NButton, NInput, NSelect } from 'naive-ui'
import { api } from '../../api/client'
import { toast } from '../../ui/feedback'

const props = defineProps({ project: { type: Object, default: null } })
const emit = defineEmits(['save'])
const category = ref('characters')
const name = ref('')
const description = ref('')
const assetId = ref(null)
const library = ref([])
const roles = ref([])
const selectedRole = ref(null)
const error = ref('')
const categories = [
  { key: 'characters', label: '角色' },
  { key: 'locations', label: '地点' },
  { key: 'styles', label: '风格' },
]
const items = computed(() => props.project?.assets?.[category.value] || [])
const options = computed(() => library.value.map((item) => ({ label: item.originalName || item.id, value: item.id })))
const roleOptions = computed(() => roles.value
  .filter((role) => !allCharacters.value.some((item) => item.roleId === role.id))
  .map((role) => ({ label: role.name, value: role.id })))
const allCharacters = computed(() => props.project?.assets?.characters || [])

watch(() => props.project?.id, async (id) => {
  if (!id) return
  try {
    if (!library.value.length) library.value = await api.get('/assets?limit=200')
    roles.value = await api.get('/characters')
  } catch {
    library.value = []
    roles.value = []
  }
}, { immediate: true })

function newId() {
  return Math.random().toString(16).slice(2, 10) + Math.random().toString(16).slice(2, 10)
}

function plain(item) {
  return {
    id: item.id,
    name: item.name,
    description: item.description || '',
    assetId: item.assetId || item.asset_id || null,
  }
}

function normalizeCharacter(item) {
  return item.roleId
    ? { id: `role:${item.roleId}`, name: item.name, roleId: item.roleId }
    : plain(item)
}

function save(key, next) {
  const base = {
    characters: allCharacters.value,
    locations: props.project?.assets?.locations || [],
    styles: props.project?.assets?.styles || [],
  }
  base[key] = next
  emit('save', {
    characters: base.characters.map(normalizeCharacter),
    locations: base.locations.map(plain),
    styles: base.styles.map(plain),
  })
}

function addItem() {
  error.value = ''
  if (!name.value.trim() || !(description.value.trim() || assetId.value)) {
    error.value = '名字必填，参考图或文字描述至少有一项'
    return
  }
  save(category.value, [...items.value, {
    id: newId(), name: name.value.trim(), description: description.value.trim(), assetId: assetId.value || null,
  }])
  name.value = ''
  description.value = ''
  assetId.value = null
}

function removeItem(id) {
  save(category.value, items.value.filter((item) => item.id !== id))
}

function addRole() {
  error.value = ''
  const role = roles.value.find((item) => item.id === selectedRole.value)
  if (!role) return
  if (allCharacters.value.some((item) => item.roleId === role.id)) {
    error.value = '这个角色已经在项目里了'
    return
  }
  save('characters', [...allCharacters.value, {
    id: `role:${role.id}`, name: role.name, roleId: role.id, description: '', assetId: null,
  }])
  selectedRole.value = null
}

async function promote(item) {
  error.value = ''
  const reference = item.assetId || item.asset_id
  try {
    await api.post('/characters', {
      name: item.name,
      aliases: [],
      appearance: {},
      images: reference ? [{ assetId: reference, angle: 'front', isPrimary: true, label: '' }] : [],
      notes: item.description || '',
    })
    roles.value = await api.get('/characters')
    toast(`已把「${item.name}」复制到角色库，其他项目可以直接引用（本项目条目保持不变）`)
  } catch (err) {
    error.value = err.message
  }
}
</script>

<template>
  <section class="asset-board">
    <header>
      <div><span>项目视觉资产</span><strong>角色可从角色库复用；参考图只出现在点到名字的镜头里</strong></div>
    </header>
    <div class="tabs">
      <button v-for="item in categories" :key="item.key" type="button" :class="{ active: category === item.key }" @click="category = item.key">{{ item.label }}</button>
    </div>
    <ul>
      <li v-for="item in items" :key="item.id" class="asset-item">
        <span class="item-name" :title="item.name">{{ item.name }}</span>
        <small>{{ item.roleId ? `角色库 · ${(item.images || []).length} 张参考图` : (item.description || '已绑参考图') }}</small>
        <span v-if="item.roleId" class="source-tag">复用</span>
        <n-button v-else-if="category === 'characters'" size="tiny" quaternary @click="promote(item)">升格到角色库</n-button>
        <button type="button" @click="removeItem(item.id)">移除</button>
      </li>
      <li v-if="!items.length" class="empty">这一类还没有登记</li>
    </ul>
    <div v-if="category === 'characters'" class="role-picker">
      <n-select v-model:value="selectedRole" size="small" filterable clearable :options="roleOptions" placeholder="从角色库挑选" />
      <n-button size="small" secondary @click="addRole">加入本项目</n-button>
      <span class="hint">老角色可用「升格到角色库」复制成可复用条目，本项目不受影响</span>
    </div>
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
li { display: flex; align-items: center; gap: 8px; font-size: 12px; }
li small { flex: 1; min-width: 0; color: #81786e; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
li button, .empty { color: #8d5a52; background: transparent; border: 0; cursor: pointer; white-space: nowrap; }
.item-name { width: 80px; flex-shrink: 0; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.source-tag { font-size: 10px; color: #315e68; background: #e6f0f1; border-radius: 3px; padding: 2px 5px; white-space: nowrap; }
.empty { display: block; color: #8c8175; }
.role-picker { display: flex; align-items: center; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.role-picker :deep(.n-select) { min-width: 200px; flex: 0 1 260px; }
.hint { color: #8c8175; font-size: 11px; }
.composer { display: grid; grid-template-columns: 1fr 1.4fr 1.4fr auto; gap: 6px; margin-top: 12px; }
p { color: #9b3e37; font-size: 12px; }
@media (max-width: 900px) { .composer { grid-template-columns: 1fr; } }
</style>
