<script setup>
import { ref, watch } from 'vue'
import WechatDraftPanel from './WechatDraftPanel.vue'

// 创作空间只选择真实支持的发布平台，具体账号与草稿提交交给发布面板。
const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
  body: { type: String, default: '' },
  contentId: { type: String, default: '' },
  versionId: { type: String, default: '' },
  coverAssetId: { type: String, default: '' },
  inlineAssetIds: { type: Array, default: () => [] },
})
const emit = defineEmits(['close', 'published'])
const selectedPlatform = ref('')
watch(() => props.open, (open) => {
  if (open) selectedPlatform.value = ''
})
</script>

<template>
  <Teleport to="body">
    <div v-if="open && !selectedPlatform" class="platform-overlay" @click.self="emit('close')">
      <section class="platform-picker" role="dialog" aria-modal="true" aria-label="选择发布平台">
        <header>
          <h2>选择发布平台</h2>
          <button type="button" aria-label="关闭" @click="emit('close')">×</button>
        </header>
        <p>选择平台后配置目标账号并推送，文章排版请使用独立排版工作台。</p>
        <button type="button" class="platform-option" @click="selectedPlatform = 'wechat_official'">
          <strong>微信公众号</strong>
          <span>推送到公众号草稿箱</span>
          <span class="platform-next">选择 →</span>
        </button>
      </section>
    </div>
  </Teleport>
  <WechatDraftPanel
    :open="open && selectedPlatform === 'wechat_official'"
    :title="title"
    :body="body"
    :content-id="contentId"
    :version-id="versionId"
    :cover-asset-id="coverAssetId"
    :inline-asset-ids="inlineAssetIds"
    @close="emit('close')"
    @published="emit('published')"
  />
</template>

<style scoped>
.platform-overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  display: grid;
  place-items: center;
  padding: 20px;
  background: var(--color-overlay);
}
.platform-picker {
  width: min(440px, 100%);
  padding: 24px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  box-shadow: var(--shadow-lg);
}
.platform-picker header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.platform-picker h2 { margin: 0; font-size: 18px; color: var(--color-text-primary); }
.platform-picker header button {
  border: 0;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 24px;
  cursor: pointer;
}
.platform-picker p { margin: 12px 0 20px; font-size: 13px; line-height: 1.7; color: var(--color-text-muted); }
.platform-option {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  padding: 16px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
  color: var(--color-text-primary);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.platform-option:hover { border-color: var(--color-accent); }
.platform-option span { font-size: 12px; color: var(--color-text-secondary); }
.platform-option .platform-next { align-self: flex-end; color: var(--color-accent); }
</style>
