<script setup>
import { ref } from 'vue'
import { NButton, NInput } from 'naive-ui'

const props = defineProps({
  creating: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

const emit = defineEmits(['submit'])

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
  emit('submit', {
    title: title.value.trim(),
    sourceText: sourceText.value,
    rightsConfirmed: true,
  })
}
</script>

<template>
  <form class="create-form" @submit.prevent="submit">
    <label class="field">
      <span class="field-label">书名</span>
      <NInput v-model:value="title" placeholder="可以稍后再起，先写「未命名」" :maxlength="60" />
    </label>

    <label class="field">
      <span class="field-label">原文</span>
      <textarea
        v-model="sourceText"
        class="source-input"
        rows="9"
        placeholder="粘贴你有权改写的原文，切章会按原文里的章节标题自动拆分"
      />
      <span class="field-hint">支持整本粘贴；没有章节标题时会按段落长度兜底切分。</span>
    </label>

    <label class="rights-check">
      <input v-model="rightsConfirmed" type="checkbox" />
      <span>我确认有权改写这份文本，并对其使用后果负责</span>
    </label>

    <p v-if="formError" class="form-error">{{ formError }}</p>
    <p v-else-if="props.error" class="form-error">{{ props.error }}</p>

    <div class="form-actions">
      <NButton type="primary" attr-type="submit" :loading="props.creating" :disabled="!rightsConfirmed || props.creating">
        创建作品
      </NButton>
    </div>
  </form>
</template>

<style scoped>
.create-form { display: flex; flex-direction: column; gap: 14px; }
.field { display: flex; flex-direction: column; gap: 6px; }
.field-label { font-size: 12px; font-weight: 600; color: var(--paper-ink-soft); letter-spacing: 0.04em; }
.field-hint { font-size: 11px; color: var(--paper-ink-muted); }
.source-input {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  min-height: 168px;
  padding: 10px 12px;
  font: 13.5px/1.8 var(--font-stack);
  border: 1px solid var(--paper-line-strong);
  border-radius: 6px;
  background: var(--paper-surface);
  color: var(--paper-ink);
}
.source-input:focus-visible { outline: 2px solid var(--paper-accent); outline-offset: 1px; }
.source-input::placeholder { color: var(--paper-ink-muted); }
.rights-check { display: flex; align-items: flex-start; gap: 8px; font-size: 12.5px; color: var(--paper-ink-soft); line-height: 1.6; cursor: pointer; }
.rights-check input { margin-top: 2px; accent-color: var(--paper-accent); }
.form-error { margin: 0; color: var(--paper-danger); font-size: 12px; }
.form-actions { display: flex; justify-content: flex-end; }
</style>
