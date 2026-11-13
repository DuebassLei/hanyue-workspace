<script setup>
import { NAlert, NButton, NTag } from 'naive-ui'
defineProps({
  form: { type: Object, required: true },
  selectedAccounts: { type: Array, required: true },
  previews: { type: Object, required: true },
  progress: { type: Object, default: null },
  results: { type: Array, required: true },
  notice: { type: String, default: '' },
  error: { type: String, default: '' },
})
defineEmits(['preview', 'submit', 'retry'])
</script>

<template>
  <section class="publish-step">
    <div class="publish-step-heading">
      <span class="publish-step-kicker">Step 3</span>
      <h2>预检与确认</h2>
      <p>确认目标账号、执行方式和内容后，再为每个账号提交独立任务。</p>
    </div>
    <div class="confirm-strip">
      <strong>{{ form.mode === 'draft' ? '推送草稿箱' : form.mode === 'record' ? '登记已发布结果' : form.mode === 'auto' ? '自动执行' : '手动执行' }}</strong>
      <span> · {{ selectedAccounts.map((account) => `${account.platformLabel}·${account.displayName}`).join('、') }}</span>
      <span v-if="form.scheduledAt"> · {{ form.scheduledAt.replace('T', ' ') }}</span>
      <span v-else> · 立即执行</span>
    </div>
    <div class="row"><n-button size="small" quaternary :disabled="!!progress" @click="$emit('preview')">重新预检</n-button></div>
    <div v-for="account in selectedAccounts" :key="account.id" class="preview-result">
      <div class="row row-tight"><strong>{{ account.platformLabel }} · {{ account.displayName }}</strong><n-tag v-if="previews[account.id]" size="small" :type="previews[account.id].issues?.some((issue) => issue.level === 'error') ? 'warning' : 'success'">{{ previews[account.id].issues?.length ? `${previews[account.id].issues.length} 项待检查` : '校验通过' }}</n-tag></div>
      <ul v-if="previews[account.id]?.issues?.length" class="issue-list"><li v-for="(issue, index) in previews[account.id].issues" :key="index">{{ issue.message }}</li></ul>
    </div>
    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>
    <p v-if="notice" class="field-hint">{{ notice }}</p>
    <div v-if="progress" class="submit-progress">正在提交 {{ progress.current }}/{{ progress.total }}</div>
    <div v-if="results.length" class="result-list">
      <div v-for="result in results" :key="result.account.id" class="result-row">
        <span>{{ result.account.displayName }}</span>
        <n-tag v-if="result.ok" size="small" type="success">成功</n-tag>
        <template v-else><n-tag size="small" type="warning">失败</n-tag><small>{{ result.error }}</small><n-button size="small" quaternary @click="$emit('retry', result)">重试</n-button></template>
      </div>
    </div>
  </section>
</template>

<style scoped>
.publish-step { display: flex; flex-direction: column; gap: var(--space-4); }
.publish-step-heading h2 { margin: 4px 0 0; color: var(--color-text-primary); }
.publish-step-heading p { margin: 6px 0 0; color: var(--color-text-muted); font-size: var(--font-sm); }
.publish-step-kicker { color: var(--color-accent); font-size: var(--font-xs); font-weight: 700; text-transform: uppercase; }
.confirm-strip { padding: 12px; border: 1px solid var(--color-border); background: var(--color-surface-raised); color: var(--color-text-secondary); font-size: var(--font-sm); }
.preview-result, .result-list { display: flex; flex-direction: column; gap: 8px; padding: 12px 0; border-bottom: 1px solid var(--color-border); }
.issue-list { margin: 0; padding-left: 20px; color: var(--color-text-secondary); font-size: var(--font-xs); }
.submit-progress { color: var(--color-accent); font-size: var(--font-sm); }
.result-row { display: grid; grid-template-columns: 1fr auto auto; align-items: center; gap: 8px; font-size: var(--font-sm); }
.result-row small { color: var(--color-danger); }
</style>
