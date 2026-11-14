<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

/**
 * 375px 手机壳预览：内嵌后端渲染好的正文 HTML（与推送同一份）。
 * 白底是微信正文的固定底色，不跟随应用深色主题。
 * full 形态顶部模拟真实阅读器：iPhone 状态栏 + 微信文章导航栏，正文仍是服务端原样渲染。
 * mini 形态供组件库卡片等密集陈列场景：钛金属薄壳、仅保留灵动岛状态条、无 Home 条，
 * 且内容不满一屏时壳高贴合内容，避免大片空黑屏。
 * scale 用外层占位盒 + 整体 transform 缩放，避免壳内二次缩放错位。
 *
 * BEZEL / STATUS_HEIGHT / NAV_HEIGHT 要和壳的 padding、顶部区域高度一致，
 * 否则占位盒会裁切或留空。
 */
const props = defineProps({
  html: { type: String, default: '' },
  busy: { type: Boolean, default: false },
  error: { type: String, default: '' },
  /** full：完整微信阅读器模拟；mini：紧凑展示壳（组件库卡片） */
  variant: { type: String, default: 'full' },
  /** 缩放倍率（组件库卡片里用 0.8 左右节省空间） */
  scale: { type: Number, default: 1 },
  /** 内容区最大高度 px（未缩放，不含状态栏和边框）；mini 下内容不足则自动收缩 */
  height: { type: Number, default: 520 },
})

const SCREEN_WIDTH = 375
const BEZEL = 10
const MINI_BEZEL = 6
// 预览壳不是 1:1 真机截图，状态栏只作设备提示，压到 32px，避免抢走正文。
const STATUS_HEIGHT = 32
const MINI_STATUS_HEIGHT = 26
const NAV_HEIGHT = 44
const HOME_HEIGHT = 18
// mini 内容低于此值也保持的最低屏幕高，防止空态/错误态塌成一条缝
const MINI_MIN_SCREEN = 140

const isMini = computed(() => props.variant === 'mini')
const bezel = computed(() => (isMini.value ? MINI_BEZEL : BEZEL))
const chromeHeight = computed(() =>
  isMini.value ? MINI_STATUS_HEIGHT : STATUS_HEIGHT + NAV_HEIGHT + HOME_HEIGHT,
)

const scrollEl = ref(null)
const contentHeight = ref(0)

/** mini 形态下内容不满一屏时壳高贴合内容；用 ResizeObserver 跟踪 v-html 布局变化 */
let resizeObserver = null

function observeContent() {
  resizeObserver?.disconnect()
  resizeObserver = null
  if (!isMini.value) return
  const el = scrollEl.value?.firstElementChild
  if (!el) return
  const read = () => {
    // offsetHeight 不受外层 transform: scale 影响，无需除以 scale
    contentHeight.value = Math.ceil(el.offsetHeight)
  }
  read()
  resizeObserver = new ResizeObserver(read)
  resizeObserver.observe(el)
}

onMounted(observeContent)
watch(
  () => [props.html, isMini.value],
  ([html]) => (html ? observeContent() : (contentHeight.value = 0)),
)

onBeforeUnmount(() => resizeObserver?.disconnect())

const screenHeight = computed(() => {
  if (!isMini.value) return props.height
  const natural = contentHeight.value || props.height
  return Math.max(MINI_MIN_SCREEN, Math.min(props.height, natural))
})

const boxStyle = computed(() => ({
  width: `${(SCREEN_WIDTH + bezel.value * 2) * props.scale}px`,
  height: `${(screenHeight.value + chromeHeight.value + bezel.value * 2) * props.scale}px`,
}))

const shellStyle = computed(() => ({
  width: `${SCREEN_WIDTH + bezel.value * 2}px`,
  padding: `${bezel.value}px`,
  borderRadius: isMini.value ? '32px' : '44px',
  transform: `scale(${props.scale})`,
  transformOrigin: 'top left',
}))

const screenStyle = computed(() => ({
  borderRadius: isMini.value ? '26px' : '34px',
}))

const wrapped = computed(() => {
  if (!props.html) return ''
  return `<div style="padding:16px 18px;background:#ffffff;color:#333;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;">${props.html}</div>`
})
</script>

<template>
  <div class="phone-box" :class="{ 'phone-box-mini': isMini }" :style="boxStyle">
    <div class="phone-shell" :class="{ 'phone-shell-mini': isMini }" :style="shellStyle">
      <div class="phone-screen" :style="screenStyle">
        <div
          class="phone-status"
          :class="{ 'phone-status-mini': isMini }"
          :style="{ height: `${isMini ? MINI_STATUS_HEIGHT : STATUS_HEIGHT}px` }"
          aria-hidden="true"
        >
          <template v-if="!isMini">
            <span class="phone-time">9:41</span>
            <span class="phone-island">
              <span class="phone-lens" />
            </span>
            <span class="phone-sys">
              <svg class="phone-ico" viewBox="0 0 17 12" width="13" height="9">
                <rect x="0" y="7.2" width="3" height="4.8" rx="0.6" />
                <rect x="4.6" y="4.8" width="3" height="7.2" rx="0.6" />
                <rect x="9.2" y="2.4" width="3" height="9.6" rx="0.6" />
                <rect x="13.8" y="0" width="3" height="12" rx="0.6" />
              </svg>
              <svg class="phone-ico" viewBox="0 0 16 12" width="12" height="9">
                <path d="M8 9.15a1.35 1.35 0 1 1 0 2.7 1.35 1.35 0 0 1 0-2.7z" />
                <path d="M4.05 7.55a5.55 5.55 0 0 1 7.9 0l-1.15 1.15a3.9 3.9 0 0 0-5.6 0L4.05 7.55z" />
                <path d="M1.55 5.05a9.05 9.05 0 0 1 12.9 0L13.3 6.2a7.4 7.4 0 0 0-10.6 0L1.55 5.05z" />
              </svg>
              <svg class="phone-ico" viewBox="0 0 27 13" width="18" height="9">
                <rect x="0.7" y="0.7" width="22.2" height="11.6" rx="2.6" fill="none" stroke="currentColor" stroke-width="1.3" />
                <rect x="2.2" y="2.2" width="16.4" height="8.6" rx="1.2" />
                <path d="M24.4 4.3v4.4c.95-.35 1.7-1.15 1.7-2.2s-.75-1.85-1.7-2.2z" />
              </svg>
            </span>
          </template>
          <span v-else class="phone-island phone-island-mini">
            <span class="phone-lens" />
          </span>
        </div>
        <div v-if="!isMini" class="phone-nav" :style="{ height: `${NAV_HEIGHT}px` }" aria-hidden="true">
          <span class="phone-back"><i /></span>
          <span class="phone-nav-title">公众号文章</span>
          <span class="phone-nav-actions"><i /><b /><i /></span>
        </div>
        <div ref="scrollEl" class="phone-scroll" :style="{ height: `${screenHeight}px` }">
          <div v-if="busy" class="phone-state">排版渲染中…</div>
          <div v-else-if="error" class="phone-state phone-state-error">{{ error }}</div>
          <div v-else-if="wrapped" class="phone-content" v-html="wrapped" />
          <div v-else class="phone-state">开始输入即可预览</div>
        </div>
        <div v-if="!isMini" class="phone-home" aria-hidden="true"><span /></div>
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
  border-radius: 44px;
  background: #1c1c1e;
  box-shadow:
    inset 0 0 0 1px rgba(255, 255, 255, 0.16),
    inset 0 1px 0 rgba(255, 255, 255, 0.28);
}
/* mini：钛金属中框质感 + 悬浮投影（投影随 transform 缩放，观感一致） */
.phone-shell-mini {
  background: linear-gradient(145deg, #dfe2e8 0%, #a9adb6 26%, #7e828c 55%, #b9bdc5 80%, #8d919a 100%);
  box-shadow:
    inset 0 0 0 1px rgba(255, 255, 255, 0.35),
    inset 0 1px 1px rgba(255, 255, 255, 0.5),
    0 18px 34px -16px rgba(23, 28, 42, 0.38),
    0 3px 8px -2px rgba(23, 28, 42, 0.16);
}
.phone-screen {
  border-radius: 34px;
  background: #ffffff;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.phone-status {
  position: relative;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  color: #111;
  background: #fff;
}
.phone-nav {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 15px;
  border-bottom: 1px solid #eeeeee;
  color: #252525;
  background: #fff;
}
.phone-nav-title {
  flex: 1;
  margin-left: 10px;
  overflow: hidden;
  font-size: 11px;
  font-weight: 500;
  line-height: 1;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.phone-back {
  position: relative;
  display: block;
  width: 14px;
  height: 16px;
}
.phone-back::before,
.phone-back::after {
  content: '';
  position: absolute;
  left: 2px;
  width: 9px;
  height: 1.5px;
  border-radius: 1px;
  background: currentColor;
  transform-origin: left center;
}
.phone-back::before {
  top: 6px;
  transform: rotate(-45deg);
}
.phone-back::after {
  top: 6px;
  transform: rotate(45deg);
}
.phone-nav-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: 10px;
}
.phone-nav-actions i {
  display: block;
  width: 11px;
  height: 1px;
  border-radius: 0;
  background: #444;
}
.phone-nav-actions b {
  display: block;
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: #444;
}
.phone-nav-actions i:first-child,
.phone-nav-actions i:last-child {
  width: 11px;
  height: 1px;
  border-radius: 0;
}
.phone-nav-actions i:first-child {
  transform: rotate(45deg) translate(2px, -2px);
}
.phone-nav-actions i:last-child {
  transform: rotate(-45deg) translate(2px, 2px);
}
.phone-home {
  position: relative;
  flex: none;
  height: 18px;
  background: #fff;
}
.phone-home span {
  position: absolute;
  left: 50%;
  bottom: 6px;
  width: 78px;
  height: 3px;
  border-radius: 999px;
  background: #111;
  transform: translateX(-50%);
}
.phone-time {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: -0.2px;
  line-height: 1;
  font-variant-numeric: tabular-nums;
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', sans-serif;
}
.phone-island {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 92px;
  height: 20px;
  transform: translate(-50%, -50%);
  border-radius: 12px;
  background: #000;
}
/* mini：灵动岛贴顶，作为唯一设备提示 */
.phone-status-mini {
  background: #fff;
}
.phone-island-mini {
  top: 7px;
  width: 64px;
  height: 16px;
  border-radius: 10px;
}
.phone-lens {
  position: absolute;
  top: 50%;
  right: 8px;
  width: 6px;
  height: 6px;
  transform: translateY(-50%);
  border-radius: 50%;
  background: radial-gradient(circle at 35% 35%, #24384a, #0b0d12 62%);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.14);
}
.phone-sys {
  display: flex;
  align-items: center;
  gap: 4px;
}
.phone-ico {
  display: block;
  fill: currentColor;
}
.phone-scroll {
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: thin;
  scrollbar-color: rgba(0, 0, 0, 0.22) transparent;
}
.phone-scroll::-webkit-scrollbar {
  width: 4px;
}
.phone-scroll::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.22);
  border-radius: 4px;
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
