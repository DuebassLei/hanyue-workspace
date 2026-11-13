<script setup>
import { watch } from 'vue'
import { NAlert, NButton } from 'naive-ui'
import StepContentSource from './StepContentSource.vue'
import StepReviewConfirm from './StepReviewConfirm.vue'
import StepTargetMode from './StepTargetMode.vue'

const props = defineProps({
  flow: { type: Object, required: true },
  open: { type: Boolean, default: false },
  title: { type: String, default: '新建发布' },
  lockedMode: { type: String, default: '' },
  lockStep1: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'submitted'])

watch(() => props.open, (open) => {
  if (open) props.flow.load()
})

watch(() => props.flow.step.value, (value) => {
  if (value === 3) props.flow.preview()
})

function toggleAccount(id) {
  props.flow.toggleAccount(id)
}

function toggleManual(value) {
  props.flow.toggleManual(value)
}

async function submit() {
  await props.flow.submit()
  emit('submitted', props.flow.results.value)
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="publish-wizard-overlay" @click.self="$emit('close')">
      <section class="publish-wizard" role="dialog" aria-modal="true" :aria-label="title">
        <header class="publish-wizard-header">
          <div><strong>{{ title }}</strong><span>统一发布向导</span></div>
          <button class="wizard-close" type="button" aria-label="关闭" @click="$emit('close')">×</button>
        </header>
        <nav class="wizard-steps" aria-label="发布步骤">
          <button v-for="item in [1, 2, 3]" :key="item" type="button" :class="{ 'is-active': flow.step.value === item }" :disabled="item > flow.step.value || (lockStep1 && item === 1)" @click="flow.step.value = item">{{ item }} {{ ['内容来源', '目标与方式', '预检确认'][item - 1] }}</button>
        </nav>
        <main class="publish-wizard-body">
          <n-alert v-if="flow.error.value" type="error" :bordered="false">{{ flow.error.value }}</n-alert>
          <StepContentSource v-if="flow.step.value === 1" :form="flow.form" :contents="flow.contents.value" :loading="flow.contentLoading.value" :locked="lockStep1" @select-content="flow.selectContent" @toggle-manual="toggleManual" />
          <StepTargetMode v-else-if="flow.step.value === 2" :form="flow.form" :accounts="flow.accounts.value" :mode-availability="flow.modeAvailability.value" :locked-mode="lockedMode" @toggle-account="toggleAccount" @update:mode="flow.setMode" />
          <StepReviewConfirm v-else :form="flow.form" :selected-accounts="flow.selectedAccounts.value" :previews="flow.previewByAccount" :progress="flow.submitProgress.value" :results="flow.results.value" :notice="flow.notice.value" :error="flow.error.value" @preview="flow.preview" @submit="submit" @retry="flow.retryResult" />
        </main>
        <footer class="publish-wizard-footer">
          <n-button quaternary @click="$emit('close')">取消</n-button>
          <span class="spacer" />
          <n-button v-if="flow.step.value > (lockStep1 ? 2 : 1)" quaternary @click="flow.back">上一步</n-button>
          <n-button v-if="flow.step.value < 3" type="primary" :disabled="!flow.canContinue.value" @click="flow.next">下一步</n-button>
          <n-button v-else type="primary" :loading="flow.busy.value" :disabled="!flow.selectedAccounts.value.length" @click="submit">确认并提交</n-button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.publish-wizard-overlay { position: fixed; inset: 0; z-index: 300; display: flex; justify-content: flex-end; background: rgba(15, 23, 42, .42); }
.publish-wizard { display: flex; width: min(720px, 100vw); height: 100vh; flex-direction: column; background: var(--color-surface); box-shadow: -10px 0 30px rgba(0,0,0,.18); }
.publish-wizard-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 18px 22px; border-bottom: 1px solid var(--color-border); }
.publish-wizard-header strong, .publish-wizard-header span { display: block; }
.publish-wizard-header strong { color: var(--color-text-primary); }
.publish-wizard-header span { margin-top: 3px; color: var(--color-text-muted); font-size: var(--font-xs); }
.wizard-close { border: 0; background: transparent; color: var(--color-text-muted); font-size: 25px; cursor: pointer; }
.wizard-steps { display: flex; gap: 4px; padding: 10px 22px; border-bottom: 1px solid var(--color-border); overflow-x: auto; }
.wizard-steps button { flex: 1; padding: 8px; border: 0; background: transparent; color: var(--color-text-muted); font: inherit; font-size: var(--font-xs); white-space: nowrap; cursor: pointer; }
.wizard-steps button.is-active { border-bottom: 2px solid var(--color-accent); color: var(--color-text-primary); font-weight: 700; }
.publish-wizard-body { flex: 1; min-height: 0; overflow-y: auto; padding: 24px; }
.publish-wizard-footer { display: flex; align-items: center; gap: 8px; padding: 14px 22px; border-top: 1px solid var(--color-border); }
.spacer { flex: 1; }
@media (max-width: 560px) { .publish-wizard-body { padding: 16px; } .publish-wizard-footer { padding: 12px 16px; } }
</style>
