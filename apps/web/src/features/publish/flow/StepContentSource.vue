<script setup>
import { computed } from 'vue'
import { NButton, NInput, NSelect } from 'naive-ui'

const props = defineProps({
  form: { type: Object, required: true },
  contents: { type: Array, required: true },
  loading: { type: Boolean, default: false },
  locked: { type: Boolean, default: false },
})
defineEmits(['select-content', 'toggle-manual'])

const contentOptions = computed(() => [
  { value: '', label: '请选择要发布的稿件' },
  ...props.contents.map((item) => ({ value: item.id, label: item.title })),
])
const bodyLocked = computed(() => !props.form.manualEntry && Boolean(props.form.contentId))
</script>

<template>
  <section class="publish-step">
    <div class="publish-step-heading">
      <span class="publish-step-kicker">Step 1</span>
      <h2>内容来源</h2>
      <p>选择创作空间稿件，或直接填写本次发布内容。</p>
    </div>
    <label v-if="!locked && !form.manualEntry" class="field">
      <span class="field-label">选择创作空间稿件</span>
      <n-select
        :value="form.contentId"
        :options="contentOptions"
        :loading="loading"
        :disabled="loading"
        @update:value="$emit('select-content', $event)"
      />
    </label>
    <n-button v-if="!locked && !form.manualEntry" text type="primary" @click="$emit('toggle-manual', true)">
      没有对应稿件，改用手动填写
    </n-button>
    <n-button v-if="!locked && form.manualEntry" text type="primary" @click="$emit('toggle-manual', false)">
      返回从创作空间选择稿件
    </n-button>
    <label v-if="form.manualEntry || locked" class="field">
      <span class="field-label">标题</span>
      <n-input v-model:value="form.title" maxlength="64" placeholder="文章标题" :readonly="bodyLocked" />
    </label>
    <label v-if="form.manualEntry || locked" class="field">
      <span class="field-label">正文</span>
      <n-input v-model:value="form.body" type="textarea" :rows="10" placeholder="正文内容" :readonly="bodyLocked" />
    </label>
    <p v-if="loading" class="field-hint">正在读取稿件内容…</p>
  </section>
</template>

<style scoped>
.publish-step {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.publish-step-heading h2 { margin: 4px 0 0; color: var(--color-text-primary); }
.publish-step-heading p { margin: 6px 0 0; color: var(--color-text-muted); font-size: var(--font-sm); }
.publish-step-kicker { color: var(--color-accent); font-size: var(--font-xs); font-weight: 700; text-transform: uppercase; }
</style>
