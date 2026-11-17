<script setup>
import { computed } from 'vue'
import { NCheckbox, NDatePicker, NEmpty, NInput, NRadio, NSelect } from 'naive-ui'

const props = defineProps({
  form: { type: Object, required: true },
  accounts: { type: Array, required: true },
  modeAvailability: { type: Object, required: true },
  lockedMode: { type: String, default: '' },
})
defineEmits(['toggle-account', 'update:mode'])

const groupedAccounts = computed(() => {
  const groups = new Map()
  props.accounts.forEach((account) => {
    if (!groups.has(account.platformLabel)) groups.set(account.platformLabel, [])
    groups.get(account.platformLabel).push(account)
  })
  return Array.from(groups, ([label, accounts]) => ({ label, accounts }))
})

const themeOptions = [
  { value: 'ink-green', label: '墨绿' },
  { value: 'warm-orange', label: '暖橙' },
  { value: 'mono', label: '简约' },
  { value: 'dispatch', label: '公文' },
]

const scheduledTs = computed({
  get: () => (props.form.scheduledAt ? Date.parse(props.form.scheduledAt) : null),
  set: (value) => {
    if (!value) {
      props.form.scheduledAt = ''
      return
    }
    const date = new Date(value)
    const pad = (part) => String(part).padStart(2, '0')
    props.form.scheduledAt = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
  },
})
</script>

<template>
  <section class="publish-step">
    <div class="publish-step-heading">
      <span class="publish-step-kicker">Step 2</span>
      <h2>目标与方式</h2>
      <p>可同时选择多个已连接账号；提交时会为每个账号生成独立任务。</p>
    </div>
    <div class="target-section">
      <span class="field-label">目标账号</span>
      <n-empty v-if="!accounts.length" description="请先在渠道平台绑定并验证账号。">
        <template #default><span class="empty-title">暂无已连接账号</span></template>
      </n-empty>
      <div v-for="group in groupedAccounts" :key="group.label" class="account-group">
        <strong>{{ group.label }}</strong>
        <label v-for="account in group.accounts" :key="account.id" class="account-option">
          <n-checkbox
            :checked="form.accountIds.includes(account.id)"
            @update:checked="$emit('toggle-account', account.id)"
          />
          <span>{{ account.displayName }}</span>
          <small>{{ account.hasOwnCredential ? `独立凭据 ${account.appIdMasked}` : '默认凭据' }}</small>
        </label>
      </div>
    </div>
    <div class="mode-grid" role="radiogroup" aria-label="发布方式">
      <label class="mode-option" :class="{ 'is-selected': form.mode === 'draft', 'is-disabled': !modeAvailability.draft }">
        <n-radio
          value="draft"
          :checked="form.mode === 'draft'"
          :disabled="!modeAvailability.draft || (lockedMode && lockedMode !== 'draft')"
          @update:checked="$emit('update:mode', 'draft')"
        />
        <strong>推送草稿箱</strong><span>公众号专属，推送后到平台后台预览与群发。</span>
      </label>
      <label class="mode-option" :class="{ 'is-selected': form.mode === 'manual' }">
        <n-radio
          value="manual"
          :checked="form.mode === 'manual'"
          :disabled="lockedMode && lockedMode !== 'manual'"
          @update:checked="$emit('update:mode', 'manual')"
        />
        <strong>排期并手动执行</strong><span>在平台侧发布完成后回填结果。</span>
      </label>
      <label class="mode-option" :class="{ 'is-selected': form.mode === 'auto', 'is-disabled': !modeAvailability.auto }">
        <n-radio
          value="auto"
          :checked="form.mode === 'auto'"
          :disabled="!modeAvailability.auto || (lockedMode && lockedMode !== 'auto')"
          @update:checked="$emit('update:mode', 'auto')"
        />
        <strong>自动执行</strong><span>仅在平台能力、授权与账号条件满足时可用。</span>
      </label>
      <label class="mode-option" :class="{ 'is-selected': form.mode === 'record' }">
        <n-radio
          value="record"
          :checked="form.mode === 'record'"
          :disabled="lockedMode && lockedMode !== 'record'"
          @update:checked="$emit('update:mode', 'record')"
        />
        <strong>登记已发布结果</strong><span>已在平台侧发布，只补充任务与结果记录。</span>
      </label>
    </div>
    <div v-if="form.mode === 'draft'" class="draft-options">
      <label class="field"><span class="field-label">排版主题</span><n-select v-model:value="form.themeId" :options="themeOptions" /></label>
      <label class="field"><span class="field-label">封面图片素材 ID</span><n-input v-model:value="form.coverAssetId" placeholder="从素材库选择的封面素材" /></label>
      <label class="field"><span class="field-label">正文图片素材 ID（逗号分隔）</span><n-input v-model:value="form.inlineAssetIdsText" placeholder="素材 ID，按正文顺序" /></label>
    </div>
    <label v-if="form.mode === 'manual' || form.mode === 'auto'" class="field">
      <span class="field-label">计划时间（可选）</span>
      <n-date-picker v-model:value="scheduledTs" type="datetime" clearable style="width: 100%" />
    </label>
    <div v-if="form.mode === 'record'" class="record-fields">
      <label class="field"><span class="field-label">平台内容 ID（可选）</span><n-input v-model:value="form.remoteContentId" /></label>
      <label class="field"><span class="field-label">链接（可选）</span><n-input v-model:value="form.permalink" placeholder="https://" /></label>
    </div>
  </section>
</template>

<style scoped>
.publish-step { display: flex; flex-direction: column; gap: var(--space-4); }
.publish-step-heading h2 { margin: 4px 0 0; color: var(--color-text-primary); }
.publish-step-heading p { margin: 6px 0 0; color: var(--color-text-muted); font-size: var(--font-sm); }
.publish-step-kicker { color: var(--color-accent); font-size: var(--font-xs); font-weight: 700; text-transform: uppercase; }
.target-section, .mode-grid, .record-fields { display: flex; flex-direction: column; gap: var(--space-2); }
.draft-options { display: grid; gap: var(--space-2); }
.account-group { display: flex; flex-direction: column; gap: 6px; padding: 10px 0; border-bottom: 1px solid var(--color-border); }
.account-group > strong { color: var(--color-text-secondary); font-size: var(--font-xs); }
.account-option { display: grid; grid-template-columns: 18px 1fr auto; align-items: center; gap: 8px; padding: 9px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); cursor: pointer; }
.account-option small { color: var(--color-text-muted); font-size: var(--font-xs); }
.mode-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-2); }
.mode-option { display: flex; flex-direction: column; gap: 5px; padding: 12px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); cursor: pointer; }
.mode-option.is-selected { border-color: var(--color-accent); background: var(--color-accent-soft); }
.mode-option.is-disabled { opacity: .58; cursor: not-allowed; }
.mode-option input { align-self: flex-start; }
.mode-option span { color: var(--color-text-muted); font-size: var(--font-xs); line-height: 1.5; }
@media (max-width: 560px) { .mode-grid { grid-template-columns: 1fr; } .account-option { grid-template-columns: 18px 1fr; } .account-option small { grid-column: 2; } }
</style>
