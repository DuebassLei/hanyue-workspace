<script setup>
import { computed } from 'vue'

/**
 * 375px 手机壳预览：内嵌后端渲染好的正文 HTML（与推送同一份）。
 * 白底是微信正文的固定底色，不跟随应用深色主题。
 * scale 用外层占位盒 + 整体 transform 缩放，避免壳内二次缩放错位。
 */
const props = defineProps({
  html: { type: String, default: '' },
  busy: { type: Boolean, default: false },
  error: { type: String, default: '' },
  /** 缩放倍率（组件库卡片里用 0.72 节省空间） */
  scale: { type: Number, default: 1 },
  /** 内容区高度 px（未缩放） */
  height: { type: Number, default: 520 },
})

const SHELL_WIDTH = 375
const SHELL_PAD = 12
const STATUS_HEIGHT = 30

const boxStyle = computed(() => ({
  width: `${(SHELL_WIDTH + SHELL_PAD) * props.scale}px`,
  height: `${(props.height + STATUS_HEIGHT + SHELL_PAD) * props.scale}px`,
}))

const shellStyle = computed(() => ({
  width: `${SHELL_WIDTH + SHELL_PAD}px`,
  transform: `scale(${props.scale})`,
  transformOrigin: 'top left',
}))

const wrapped = computed(() => {
  if (!props.html) return ''
  return `<div style="padding:16px 18px;background:#ffffff;color:#333;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;">${props.html}</div>`
})
</script>

<template>
  <div class="phone-box" :style="boxStyle">
    <div class="phone-shell" :style="shellStyle">
      <div class="phone-status">
        <span class="phone-time">9:41</span>
        <span class="phone-notch" aria-hidden="true" />
        <span class="phone-icons">▮▮ ⌁</span>
      </div>
      <div class="phone-screen" :style="{ height: `${height}px` }">
        <div v-if="busy" class="phone-state">排版渲染中…</div>
        <div v-else-if="error" class="phone-state phone-state-error">{{ error }}</div>
        <div v-else-if="wrapped" class="phone-content" v-html="wrapped" />
        <div v-else class="phone-state">开始输入即可预览</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.phone-box {
  flex: none;
  overflow: hidden;
}
.phone-shell {
  border: 1px solid var(--color-border, #d9d9d9);
  border-radius: 28px;
  background: var(--color-surface, #fff);
  padding: 6px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
}
.phone-status {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 26px;
  padding: 0 18px 2px;
  font-size: 12px;
  color: #666;
}
.phone-notch {
  width: 90px;
  height: 6px;
  border-radius: 999px;
  background: #e3e3e3;
}
.phone-icons {
  letter-spacing: 2px;
  font-size: 10px;
}
.phone-screen {
  border-radius: 18px;
  background: #ffffff;
  overflow-y: auto;
  overflow-x: hidden;
}
.phone-content :deep(img) {
  max-width: 100%;
}
.phone-state {
  padding: 48px 24px;
  text-align: center;
  color: #999;
  font-size: 13px;
}
.phone-state-error {
  color: #c0392b;
}
</style>
