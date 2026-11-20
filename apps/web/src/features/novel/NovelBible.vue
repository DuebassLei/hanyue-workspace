<script setup>
import { ref, watch } from 'vue'
import { NInput, NSelect } from 'naive-ui'

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

function setName(character, state) {
  if (character.state === state) return
  emit('patch-character', character.id, { state })
}

const stateOptions = [
  { value: 'present', label: '在场' },
  { value: 'dead', label: '死亡' },
  { value: 'absent', label: '离场' },
]
</script>

<template>
  <section class="panel">
    <header class="panel-head">
      <span>设定</span>
      <em>改名后用于续写</em>
    </header>

    <div class="panel-body">
      <div class="rules">
        <label class="rule">
          <span>人称</span>
          <NInput v-model:value="person" size="small" placeholder="第三人称" @blur="saveRules" />
        </label>
        <label class="rule">
          <span>文风</span>
          <NInput v-model:value="style" size="small" placeholder="冷峻短句" @blur="saveRules" />
        </label>
        <label class="rule rule-wide">
          <span>禁区</span>
          <NInput v-model:value="banned" size="small" placeholder="不要出现的内容" @blur="saveRules" />
        </label>
      </div>

      <div class="group">
        <h3 class="group-title">人物 <span>{{ characters.length }}</span></h3>
        <p v-if="!characters.length" class="empty">还没有抽取到人物，点顶栏「抽取人名」。</p>
        <ul v-else class="entities">
          <li v-for="character in characters" :key="character.id" class="entity">
            <div class="entity-head">
              <strong>{{ character.originalName }}</strong>
              <NSelect
                class="state-select"
                size="tiny"
                :value="character.state"
                :options="stateOptions"
                :consistent-menu-width="false"
                aria-label="人物状态"
                @update:value="(value) => setName(character, value)"
              />
            </div>
            <NInput v-model:value="names[character.id]" size="small" placeholder="新名字" @blur="saveName(character)" />
          </li>
        </ul>
      </div>

      <div class="group">
        <h3 class="group-title">别名 <span>{{ aliases.length }}</span></h3>
        <p v-if="!aliases.length" class="empty">还没有别名。</p>
        <ul v-else class="entities">
          <li v-for="alias in aliases" :key="alias.id" class="entity">
            <div class="entity-head">
              <strong>{{ alias.originalName }}</strong>
              <span class="kind">{{ alias.kind }}</span>
            </div>
            <NInput v-model:value="aliasNames[alias.id]" size="small" placeholder="新名字" @blur="saveAlias(alias)" />
          </li>
        </ul>
      </div>
    </div>
  </section>
</template>

<style scoped>
.panel { min-width: 0; border: 1px solid var(--paper-line); border-radius: 8px; background: var(--paper-surface); overflow: hidden; }
.panel-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; padding: 10px 14px; background: var(--paper-header); border-bottom: 1px solid var(--paper-line); }
.panel-head span { font-size: 12px; font-weight: 650; letter-spacing: 0.08em; color: var(--paper-ink-soft); }
.panel-head em { font-style: normal; font-size: 10.5px; color: var(--paper-ink-muted); }
.panel-body { padding: 12px 14px 14px; }

.rules { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.rule { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.rule-wide { grid-column: 1 / -1; }
.rule span { font-size: 11px; color: var(--paper-ink-muted); }

.group { margin-top: 14px; padding-top: 12px; border-top: 1px dashed var(--paper-line-strong); }
.group-title { display: flex; align-items: center; gap: 6px; margin: 0 0 8px; font-size: 12px; font-weight: 600; color: var(--paper-ink-soft); }
.group-title span { padding: 0 5px; border-radius: 999px; background: var(--paper-thumb); color: var(--paper-ink-muted); font-size: 10.5px; font-weight: 500; }
.empty { margin: 0; color: var(--paper-ink-muted); font-size: 11.5px; line-height: 1.7; }
.entities { list-style: none; display: flex; flex-direction: column; gap: 10px; margin: 0; padding: 0; }
.entity { display: flex; flex-direction: column; gap: 5px; }
.entity-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.entity-head strong { font-family: 'Source Han Serif SC', 'Noto Serif SC', 'Songti SC', Georgia, serif; font-size: 13px; font-weight: 650; color: var(--paper-ink); }
.state-select { width: 82px; flex: 0 0 auto; }
.kind { font-size: 10.5px; color: var(--paper-ink-muted); }
</style>
