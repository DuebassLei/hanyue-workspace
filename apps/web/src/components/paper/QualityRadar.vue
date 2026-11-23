<script setup>
/**
 * 本地质检维度雷达图。
 *
 * 用 ECharts 替换原先手写的 180×180 SVG：平滑多边形、渐变面积、分层网格、
 * hover 提示，且颜色全部读 CSS 变量，跟随亮暗主题自动切换。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { RadarChart } from 'echarts/charts'
import { TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([RadarChart, TooltipComponent, CanvasRenderer])

const props = defineProps({
  // [{ label, score }]，score ∈ [0, max]
  dimensions: { type: Array, default: () => [] },
  max: { type: Number, default: 100 },
  height: { type: String, default: '240px' },
})

const el = ref(null)
let chart = null
let resizeObserver = null
let themeObserver = null
// 主题属性变化时自增，触发 watch 重建 option
const themeTick = ref(0)

function cssVar(name, fallback = '') {
  if (typeof window === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

/** 把任意颜色（#rgb/#rrggbb/rgb()/rgba()）加上指定透明度，ECharts 画布不支持 color-mix。 */
function withAlpha(color, alpha) {
  if (!color) return `rgba(0,0,0,${alpha})`
  const hex = color.trim()
  if (hex.startsWith('#')) {
    let r, g, b
    if (hex.length === 4) {
      r = parseInt(hex[1] + hex[1], 16)
      g = parseInt(hex[2] + hex[2], 16)
      b = parseInt(hex[3] + hex[3], 16)
    } else {
      r = parseInt(hex.slice(1, 3), 16)
      g = parseInt(hex.slice(3, 5), 16)
      b = parseInt(hex.slice(5, 7), 16)
    }
    return `rgba(${r},${g},${b},${alpha})`
  }
  const rgb = hex.match(/rgba?\(([^)]+)\)/)
  if (rgb) {
    const parts = rgb[1].split(',').map((p) => p.trim())
    return `rgba(${parts[0]},${parts[1]},${parts[2]},${alpha})`
  }
  return hex
}

function buildOption() {
  const accent = cssVar('--paper-accent', '#315e68')
  const accentTint = cssVar('--paper-accent-tint', '#f1f6f5')
  const inkSoft = cssVar('--paper-ink-soft', '#5c564e')
  const lineStrong = cssVar('--paper-line-strong', '#d9d6cf')
  const surface = cssVar('--color-surface', '#ffffff')

  const indicator = props.dimensions.map((item) => ({ name: item.label, max: props.max }))
  const values = props.dimensions.map((item) => Number(item.score) || 0)

  return {
    tooltip: {
      trigger: 'item',
      backgroundColor: surface,
      borderColor: lineStrong,
      borderWidth: 1,
      padding: [6, 10],
      textStyle: { color: inkSoft, fontSize: 12 },
      formatter: () => {
        const rows = indicator
          .map((item, index) => `${item.name}：<b>${values[index]}</b>`)
          .join('<br/>')
        return `<b>本地质检维度</b><br/>${rows}`
      },
    },
    radar: {
      indicator,
      shape: 'polygon',
      radius: '64%',
      center: ['50%', '52%'],
      splitNumber: 4,
      axisName: {
        color: inkSoft,
        fontSize: 12,
        padding: [2, 5],
      },
      splitLine: { lineStyle: { color: lineStrong, width: 1 } },
      splitArea: {
        show: true,
        areaStyle: { color: [withAlpha(accentTint, 0.6), 'transparent'] },
      },
      axisLine: { lineStyle: { color: lineStrong, width: 1 } },
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            value: values,
            name: '得分',
            symbol: 'circle',
            symbolSize: 6,
            lineStyle: { color: accent, width: 2 },
            itemStyle: { color: accent, borderColor: surface, borderWidth: 1 },
            areaStyle: {
              color: {
                type: 'radial',
                x: 0.5,
                y: 0.5,
                r: 0.75,
                colorStops: [
                  { offset: 0, color: withAlpha(accent, 0.06) },
                  { offset: 1, color: withAlpha(accent, 0.3) },
                ],
              },
            },
            label: {
              show: true,
              color: accent,
              fontSize: 11,
              fontWeight: 600,
              formatter: '{c}',
            },
          },
        ],
      },
    ],
  }
}

function render() {
  if (!chart) return
  if (!props.dimensions.length) {
    chart.clear()
    return
  }
  chart.setOption(buildOption(), true)
}

onMounted(() => {
  if (!el.value) return
  chart = echarts.init(el.value)
  render()

  resizeObserver = new ResizeObserver(() => chart?.resize())
  resizeObserver.observe(el.value)

  // 亮暗主题（以及主色）变化时重读 CSS 变量重绘
  themeObserver = new MutationObserver(() => {
    themeTick.value += 1
  })
  themeObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme', 'data-accent', 'style'],
  })
})

watch(
  [() => props.dimensions, themeTick],
  () => render(),
  { deep: true },
)

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  themeObserver?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div ref="el" class="quality-radar-chart" :style="{ height }" role="img" aria-label="本地质检维度雷达图" />
</template>

<style scoped>
.quality-radar-chart {
  width: 100%;
  min-height: 180px;
}
</style>
