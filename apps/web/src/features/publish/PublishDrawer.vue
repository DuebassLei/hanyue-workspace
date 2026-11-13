<script setup>
import WechatDraftPanel from './WechatDraftPanel.vue'

/**
 * 创作空间发布入口的薄壳。
 * 公众号草稿箱的排版与素材编辑由专属面板维护，本入口只稳定公开契约。
 */
defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
  body: { type: String, default: '' },
  contentId: { type: String, default: '' },
  coverAssetId: { type: String, default: '' },
  inlineAssetIds: { type: Array, default: () => [] },
})
defineEmits(['close', 'published'])
</script>

<template>
  <WechatDraftPanel
    :open="open"
    :title="title"
    :body="body"
    :content-id="contentId"
    :cover-asset-id="coverAssetId"
    :inline-asset-ids="inlineAssetIds"
    @close="$emit('close')"
    @published="$emit('published')"
  />
</template>
