<script setup>
import { ref } from 'vue'
import { NButton, NInput } from 'naive-ui'

defineProps({
  projects: { type: Array, default: () => [] },
  activeId: { type: String, default: '' },
  creating: { type: Boolean, default: false },
})

const emit = defineEmits(['create', 'select', 'delete'])

const title = ref('')
const sourceText = ref('')
const rightsConfirmed = ref(false)
const formError = ref('')

function submit() {
  formError.value = ''
  if (!rightsConfirmed.value) {
    formError.value = '请先确认你有权改写这份文本'
    return
  }
  if (!sourceText.value.trim()) {
    formError.value = '原文不能为空'
    return
  }
  emit('create', {
    title: title.value.trim(),
    sourceText: sourceText.value,
    rightsConfirmed: true,
  })
}

function reset() {
  title.value = ''
  sourceText.value = ''
  rightsConfirmed.value = false
}

defineExpose({ reset })
</script>

<template>
  <section class="novel-list">
    <h2 class="block-title">项目</h2>
    <form class="create-form" @submit.prevent="submit">
      <NInput v-model:value="title" placeholder="小说标题（可空）" />
      <textarea v-model="sourceText" class="source-input" rows="6" placeholder="粘贴你有权改写的原文" />
      <label class="rights-check">
        <input v-model="rightsConfirmed" type="checkbox" />
        我有权改写这份文本
      </label>
      <p v-if="formError" class="form-error">{{ formError }}</p>
      <NButton type="primary" attr-type="submit" :loading="creating" :disabled="!rightsConfirmed || creating">新建</NButton>
    </form>
    <p v-if="!projects.length" class="empty-hint">从一份你有权改写的文本开始</p>
    <ul v-else class="project-items">
      <li v-for="item in projects" :key="item.id">
        <button type="button" class="project-row" :class="{ 'is-active': item.id === activeId }" @click="emit('select', item.id)">
          <strong>{{ item.title }}</strong>
          <small>{{ item.chapterCount || 0 }} 章</small>
        </button>
        <NButton size="tiny" quaternary type="error" @click="emit('delete', item.id)">删除</NButton>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.novel-list { min-width: 0; }
.block-title { margin: 0 0 10px; font-size: 15px; }
.create-form { display: flex; flex-direction: column; gap: 8px; }
.source-input { width: 100%; box-sizing: border-box; resize: vertical; padding: 8px; font: inherit; border: 1px solid #d9d6cf; background: #fff; color: inherit; }
.rights-check { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.form-error { margin: 0; color: #b44336; font-size: 12px; }
.empty-hint { margin: 14px 0 0; color: #827a71; font-size: 13px; }
.project-items { list-style: none; margin: 14px 0 0; padding: 0; }
.project-items li { display: flex; align-items: center; gap: 6px; border-bottom: 1px solid #e1ddd6; }
.project-row { flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 8px 4px; border: 0; background: transparent; text-align: left; cursor: pointer; color: inherit; }
.project-row.is-active { background: #f3efe7; }
.project-row small { color: #888077; font-size: 11px; }
</style>
