<script setup>
import { ref, watch } from 'vue'
import { NButton, NInput } from 'naive-ui'

const props = defineProps({
  rules: { type: Object, default: () => ({ person: '', style: '', banned: '' }) },
  characters: { type: Array, default: () => [] },
  aliases: { type: Array, default: () => [] },
})

const emit = defineEmits(['patch-rules', 'patch-character', 'patch-alias'])

const person = ref('')
const style = ref('')
const banned = ref('')
const names = ref({})
const aliasNames = ref({})

watch(
  () => props.rules,
  (value) => {
    person.value = value?.person || ''
    style.value = value?.style || ''
    banned.value = value?.banned || ''
  },
  { immediate: true },
)

watch(
  () => props.characters,
  (list) => {
    const next = {}
    for (const item of list) next[item.id] = item.newName || ''
    names.value = next
  },
  { immediate: true },
)

watch(
  () => props.aliases,
  (list) => {
    const next = {}
    for (const item of list) next[item.id] = item.newName || ''
    aliasNames.value = next
  },
  { immediate: true },
)

function saveRules() {
  const current = props.rules || {}
  if (person.value === (current.person || '') && style.value === (current.style || '') && banned.value === (current.banned || '')) return
  emit('patch-rules', { person: person.value, style: style.value, banned: banned.value })
}

function saveName(character) {
  const next = names.value[character.id] ?? ''
  if (next === (character.newName || '')) return
  emit('patch-character', character.id, { newName: next })
}

function saveAlias(alias) {
  const next = aliasNames.value[alias.id] ?? ''
  if (next === (alias.newName || '')) return
  emit('patch-alias', alias.id, { newName: next })
}

const STATE_OPTIONS = [
  { value: 'present', label: '在场' },
  { value: 'dead', label: '死亡' },
  { value: 'absent', label: '离场' },
]
</script>

<template>
  <section class="novel-bible">
    <h2 class="block-title">设定</h2>
    <div class="rules">
      <label>人称 <NInput v-model:value="person" size="small" placeholder="如：第三人称" @blur="saveRules" /></label>
      <label>文风 <NInput v-model:value="style" size="small" placeholder="如：冷峻短句" @blur="saveRules" /></label>
      <label>禁区 <NInput v-model:value="banned" size="small" placeholder="不要出现的内容" @blur="saveRules" /></label>
    </div>

    <h3>人物</h3>
    <p v-if="!characters.length" class="muted">还没有抽取到人物</p>
    <ul v-else class="entity-list">
      <li v-for="character in characters" :key="character.id">
        <strong>{{ character.originalName }}</strong>
        <NInput v-model:value="names[character.id]" size="small" placeholder="新名字" @blur="saveName(character)" />
        <div class="state-row">
          <NButton
            v-for="option in STATE_OPTIONS"
            :key="option.value"
            size="tiny"
            :type="character.state === option.value ? 'primary' : 'default'"
            :secondary="character.state !== option.value"
            @click="character.state !== option.value && emit('patch-character', character.id, { state: option.value })"
          >{{ option.label }}</NButton>
        </div>
      </li>
    </ul>

    <h3>别名</h3>
    <p v-if="!aliases.length" class="muted">还没有别名</p>
    <ul v-else class="entity-list">
      <li v-for="alias in aliases" :key="alias.id">
        <strong>{{ alias.originalName }}</strong>
        <small>{{ alias.kind }}</small>
        <NInput v-model:value="aliasNames[alias.id]" size="small" placeholder="新名字" @blur="saveAlias(alias)" />
      </li>
    </ul>
  </section>
</template>

<style scoped>
.novel-bible { min-width: 0; }
.block-title { margin: 0 0 10px; font-size: 15px; }
.rules { display: flex; flex-direction: column; gap: 8px; }
.rules label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--paper-ink-soft); }
h3 { margin: 16px 0 8px; font-size: 13px; }
.muted { margin: 0; color: var(--paper-ink-muted); font-size: 12px; }
.entity-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.entity-list li { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; min-width: 0; }
.entity-list strong { font-size: 13px; }
.entity-list small { color: var(--paper-ink-muted); font-size: 11px; }
.entity-list :deep(.n-input) { flex: 1 1 120px; min-width: 0; }
.state-row { display: flex; gap: 4px; }
</style>
