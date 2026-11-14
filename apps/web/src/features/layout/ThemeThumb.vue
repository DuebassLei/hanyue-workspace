<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

/**
 * 主题迷你样张：把后端渲染好的正文 HTML 按 375px 宽度等比缩放进卡片。
 * 为什么缩放而不是直接 100% 宽：公众号正文样式按手机宽度调校，
 * 直接压窄会让字号看起来偏大，卡片之间也无法横向对比。
 */
const props = defineProps({
  html: { type: String, default: '' },
  height: { type: Number, default: 150 },
})

const BASE_WIDTH = 375
const box = ref(null)
const scale = ref(0.58)
let observer = null

onMounted(() => {
  if (!box.value || typeof ResizeObserver === 'undefined') return
  observer = new ResizeObserver((entries) => {
    const width = entries[0]?.contentRect?.width || 0
    if (width > 0) scale.value = Math.min(1, width / BASE_WIDTH)
  })
  observer.observe(box.value)
})

onBeforeUnmount(() => observer?.disconnect())

const innerStyle = computed(() => ({
  // 绝对定位：375px 的内层宽度不能参与父级 min-content 计算，否则会撑爆栅格列宽
  position: 'absolute',
  top: '0',
  left: '0',
  width: `${BASE_WIDTH}px`,
  transform: `scale(${scale.value})`,
  transformOrigin: 'top left',
}))

const wrapped = computed(() => {
  if (!props.html) return ''
  return `<div style="padding:24px 22px;background:#ffffff;color:#333;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;">${props.html}</div>`
})
</script>

<template>
  <div ref="box" class="theme-thumb" :style="{ height: `${height}px` }">
    <div v-if="wrapped" class="theme-thumb-inner" :style="innerStyle" v-html="wrapped" />
    <div v-else class="theme-thumb-skeleton" aria-hidden="true">
      <span class="skeleton-line is-title" />
      <span class="skeleton-line" />
      <span class="skeleton-line is-short" />
    </div>
    <div class="theme-thumb-fade" aria-hidden="true" />
  </div>
</template>

<style scoped>
.theme-thumb {
  position: relative;
  overflow: hidden;
  background: #ffffff;
  border-radius: 8px 8px 0 0;
}
.theme-thumb-inner :deep(img) {
  max-width: 100%;
}
.theme-thumb-fade {
  position: absolute;
  inset: auto 0 0 0;
  height: 34px;
  background: linear-gradient(to bottom, rgba(255, 255, 255, 0), #ffffff 88%);
  pointer-events: none;
}
.theme-thumb-skeleton {
  padding: 18px 16px;
  display: flex;
  flex-direction: column;
  gap: 9px;
}
.skeleton-line {
  height: 7px;
  border-radius: 3px;
  background: #f0f0f0;
  width: 88%;
}
.skeleton-line.is-title {
  height: 15px;
  width: 62%;
  background: #e6e6e6;
}
.skeleton-line.is-short {
  width: 54%;
}
</style>
