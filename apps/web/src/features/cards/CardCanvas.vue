<script setup>
import { computed, reactive } from 'vue'

const props = defineProps({
  card: { type: Object, required: true },
  preset: { type: Object, required: true },
  template: { type: Object, required: true },
  meta: { type: Object, required: true },
  isExporting: { type: Boolean, default: false },
})

const TOKEN_RE = /(\*\*[^*]+\*\*|==[^=]+==|`[^`]+`|\*[^*]+\*|\[[^\]]+\]\([^)]+\))/g

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function renderInline(text) {
  const t = props.template
  return String(text ?? '')
    .split(TOKEN_RE)
    .map((part) => {
      if (!part) return ''
      if (part.startsWith('**') && part.endsWith('**')) {
        return `<strong style="color:${t.textColor}">${escapeHtml(part.slice(2, -2))}</strong>`
      }
      if (part.startsWith('==') && part.endsWith('==')) {
        return `<mark style="background:${t.accentBgSoft};color:${t.accentColor};box-shadow:inset 0 -2px 0 ${t.accentColor}40">${escapeHtml(
          part.slice(2, -2),
        )}</mark>`
      }
      if (part.startsWith('`') && part.endsWith('`')) {
        return `<code style="background:${t.cardSurfaceBg};border-color:${t.tableBorderColor};color:${t.accentColor}">${escapeHtml(
          part.slice(1, -1),
        )}</code>`
      }
      if (part.startsWith('*') && part.endsWith('*')) {
        return `<em>${escapeHtml(part.slice(1, -1))}</em>`
      }
      const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
      if (link) {
        return `<span class="md-link" style="color:${t.accentColor}">${escapeHtml(link[1])}</span>`
      }
      return escapeHtml(part)
    })
    .join('')
}

const failedImages = reactive(new Set())

const scale = computed(() => props.meta.fontSizeScale || 1)
const fontClass = computed(() =>
  props.template.fontMode === 'editorial-serif'
    ? 'font-serif'
    : props.template.fontMode === 'technical-mono'
      ? 'font-mono'
      : 'font-sans',
)
const patternId = computed(
  () => `pat-${props.template.id}-${props.template.patternType}-${props.card.index}`,
)
const sprocketHoles = computed(() =>
  Array.from({ length: Math.floor((props.preset.height - 40) / 36) }, (_, i) => 24 + i * 36),
)
const headingFontSize = computed(() => Math.round(21 * scale.value))
const safeZoneLabel = computed(
  () =>
    `SAFE ZONE ${props.preset.width - props.template.paddingX * 2}×${
      props.preset.height - props.template.paddingY * 2
    }px`,
)

function px(value) {
  return `${value}px`
}
</script>

<template>
  <div
    class="card-canvas"
    :data-card-index="card.index"
    :class="fontClass"
    :style="{
      width: px(preset.width),
      height: px(preset.height),
      background: template.bgStyle || template.bgColorHex,
      color: template.textColor,
      borderRadius: px(template.borderRadius || 0),
      paddingLeft: px(template.paddingX),
      paddingRight: px(template.paddingX),
      paddingTop: px(template.paddingY),
      paddingBottom: px(template.paddingY),
    }"
  >
    <!-- 1. 自定义背景底图 -->
    <img
      v-if="template.customBgImage"
      class="layer-bg"
      :src="template.customBgImage"
      alt="卡片背景图"
      referrerpolicy="no-referrer"
      crossorigin="anonymous"
      :style="{ opacity: template.bgImageOpacity ?? 0.35 }"
    />

    <!-- 2. SVG 底纹 -->
    <svg
      v-if="template.patternType !== 'none'"
      class="layer-svg"
      :width="preset.width"
      :height="preset.height"
      xmlns="http://www.w3.org/2000/svg"
      :style="{ opacity: template.patternOpacity }"
    >
      <defs>
        <pattern
          v-if="template.patternType === 'grid'"
          :id="patternId"
          width="20"
          height="20"
          patternUnits="userSpaceOnUse"
        >
          <path d="M 20 0 L 0 0 0 20" fill="none" :stroke="template.patternColor" stroke-width="0.75" />
        </pattern>
        <pattern
          v-else-if="template.patternType === 'dots'"
          :id="patternId"
          width="16"
          height="16"
          patternUnits="userSpaceOnUse"
        >
          <circle cx="2.5" cy="2.5" r="1.1" :fill="template.patternColor" />
        </pattern>
        <pattern
          v-else-if="template.patternType === 'ruled-lines'"
          :id="patternId"
          width="40"
          height="24"
          patternUnits="userSpaceOnUse"
        >
          <line x1="0" y1="23.5" x2="40" y2="23.5" :stroke="template.patternColor" stroke-width="0.8" />
        </pattern>
        <pattern
          v-else-if="template.patternType === 'paper-grain'"
          :id="patternId"
          width="32"
          height="32"
          patternUnits="userSpaceOnUse"
        >
          <path
            d="M2 6h5M18 4l4 2M8 18h6M24 22l-5 2M4 28l3-2M26 12h4"
            :stroke="template.patternColor"
            stroke-width="0.75"
            stroke-linecap="round"
          />
          <circle cx="14" cy="10" r="0.6" :fill="template.patternColor" />
          <circle cx="28" cy="28" r="0.6" :fill="template.patternColor" />
        </pattern>
        <pattern
          v-else-if="template.patternType === 'crosshairs'"
          :id="patternId"
          width="40"
          height="40"
          patternUnits="userSpaceOnUse"
        >
          <path
            d="M20 16v8M16 20h8"
            :stroke="template.patternColor"
            stroke-width="0.85"
            stroke-linecap="round"
          />
        </pattern>
        <pattern
          v-else-if="template.patternType === 'diagonal'"
          :id="patternId"
          width="14"
          height="14"
          patternUnits="userSpaceOnUse"
        >
          <path d="M-2,2 l4,-4 M0,14 l14,-14 M12,16 l4,-4" :stroke="template.patternColor" stroke-width="0.8" />
        </pattern>
      </defs>
      <rect width="100%" height="100%" :fill="`url(#${patternId})`" />
    </svg>

    <!-- 3. 边框装饰层 -->
    <svg
      v-if="template.borderFrameType === 'double-editorial'"
      class="layer-svg"
      :viewBox="`0 0 ${preset.width} ${preset.height}`"
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect
        :x="template.borderInset"
        :y="template.borderInset"
        :width="preset.width - template.borderInset * 2"
        :height="preset.height - template.borderInset * 2"
        fill="none"
        :stroke="template.borderColor"
        stroke-width="1.25"
        opacity="0.7"
      />
      <rect
        :x="template.borderInset + 4"
        :y="template.borderInset + 4"
        :width="preset.width - template.borderInset * 2 - 8"
        :height="preset.height - template.borderInset * 2 - 8"
        fill="none"
        :stroke="template.borderColor"
        stroke-width="0.6"
        opacity="0.45"
      />
      <rect :x="template.borderInset - 1.5" :y="template.borderInset - 1.5" width="5" height="5" :fill="template.borderColor" opacity="0.8" />
      <rect :x="preset.width - template.borderInset - 3.5" :y="template.borderInset - 1.5" width="5" height="5" :fill="template.borderColor" opacity="0.8" />
      <rect :x="template.borderInset - 1.5" :y="preset.height - template.borderInset - 3.5" width="5" height="5" :fill="template.borderColor" opacity="0.8" />
      <rect
        :x="preset.width - template.borderInset - 3.5"
        :y="preset.height - template.borderInset - 3.5"
        width="5"
        height="5"
        :fill="template.borderColor"
        opacity="0.8"
      />
    </svg>

    <svg
      v-else-if="template.borderFrameType === 'viewfinder-corners'"
      class="layer-svg"
      :viewBox="`0 0 ${preset.width} ${preset.height}`"
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect
        :x="template.borderInset"
        :y="template.borderInset"
        :width="preset.width - template.borderInset * 2"
        :height="preset.height - template.borderInset * 2"
        fill="none"
        :stroke="template.borderColor"
        stroke-width="0.6"
        opacity="0.22"
      />
      <path
        :d="`M ${template.borderInset} ${template.borderInset + 18} L ${template.borderInset} ${template.borderInset} L ${template.borderInset + 18} ${template.borderInset}
          M ${preset.width - template.borderInset - 18} ${template.borderInset} L ${preset.width - template.borderInset} ${template.borderInset} L ${preset.width - template.borderInset} ${template.borderInset + 18}
          M ${template.borderInset} ${preset.height - template.borderInset - 18} L ${template.borderInset} ${preset.height - template.borderInset} L ${template.borderInset + 18} ${preset.height - template.borderInset}
          M ${preset.width - template.borderInset - 18} ${preset.height - template.borderInset} L ${preset.width - template.borderInset} ${preset.height - template.borderInset} L ${preset.width - template.borderInset} ${preset.height - template.borderInset - 18}`"
        fill="none"
        :stroke="template.borderColor"
        stroke-width="2.2"
        stroke-linecap="square"
      />
      <line
        :x1="preset.width / 2 - 6"
        :y1="template.borderInset"
        :x2="preset.width / 2 + 6"
        :y2="template.borderInset"
        :stroke="template.borderColor"
        stroke-width="1.5"
      />
      <line
        :x1="preset.width / 2 - 6"
        :y1="preset.height - template.borderInset"
        :x2="preset.width / 2 + 6"
        :y2="preset.height - template.borderInset"
        :stroke="template.borderColor"
        stroke-width="1.5"
      />
    </svg>

    <svg
      v-else-if="template.borderFrameType === 'stamp-perforated'"
      class="layer-svg"
      :viewBox="`0 0 ${preset.width} ${preset.height}`"
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect
        :x="template.borderInset"
        :y="template.borderInset"
        :width="preset.width - template.borderInset * 2"
        :height="preset.height - template.borderInset * 2"
        rx="6"
        fill="none"
        :stroke="template.borderColor"
        stroke-width="1.5"
        stroke-dasharray="5 4"
        opacity="0.65"
      />
      <rect
        :x="template.borderInset + 5"
        :y="template.borderInset + 5"
        :width="preset.width - template.borderInset * 2 - 10"
        :height="preset.height - template.borderInset * 2 - 10"
        rx="3"
        fill="none"
        :stroke="template.borderColor"
        stroke-width="0.75"
        opacity="0.35"
      />
    </svg>

    <svg
      v-else-if="template.borderFrameType === 'film-sprocket'"
      class="layer-svg"
      :viewBox="`0 0 ${preset.width} ${preset.height}`"
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect
        x="22"
        :y="template.borderInset"
        :width="preset.width - 44"
        :height="preset.height - template.borderInset * 2"
        fill="none"
        :stroke="template.borderColor"
        stroke-width="1.1"
        opacity="0.45"
      />
      <g v-for="y in sprocketHoles" :key="y" opacity="0.55">
        <rect x="7" :y="y" width="9" height="13" rx="2" fill="none" :stroke="template.borderColor" stroke-width="1.2" />
        <rect :x="preset.width - 16" :y="y" width="9" height="13" rx="2" fill="none" :stroke="template.borderColor" stroke-width="1.2" />
      </g>
      <text
        :x="preset.width - 11"
        :y="preset.height - 10"
        font-size="7"
        font-family="monospace"
        :fill="template.borderColor"
        text-anchor="middle"
        opacity="0.65"
      >
        {{ String(card.index).padStart(2, '0') }}A
      </text>
    </svg>

    <svg
      v-else-if="template.borderFrameType === 'gallery-filigree'"
      class="layer-svg"
      :viewBox="`0 0 ${preset.width} ${preset.height}`"
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect
        :x="template.borderInset + 3"
        :y="template.borderInset + 3"
        :width="preset.width - template.borderInset * 2 - 6"
        :height="preset.height - template.borderInset * 2 - 6"
        fill="none"
        :stroke="template.borderColor"
        stroke-width="0.9"
        opacity="0.55"
      />
      <path
        :d="`M ${template.borderInset} ${template.borderInset + 14} H ${template.borderInset + 14} V ${template.borderInset}
          M ${preset.width - template.borderInset} ${template.borderInset + 14} H ${preset.width - template.borderInset - 14} V ${template.borderInset}
          M ${template.borderInset} ${preset.height - template.borderInset - 14} H ${template.borderInset + 14} V ${preset.height - template.borderInset}
          M ${preset.width - template.borderInset} ${preset.height - template.borderInset - 14} H ${preset.width - template.borderInset - 14} V ${preset.height - template.borderInset}`"
        fill="none"
        :stroke="template.borderColor"
        stroke-width="1.2"
        opacity="0.85"
      />
    </svg>

    <svg
      v-else-if="template.borderFrameType === 'swiss-grid'"
      class="layer-svg"
      :viewBox="`0 0 ${preset.width} ${preset.height}`"
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect
        :x="template.borderInset"
        :y="template.borderInset"
        :width="preset.width - template.borderInset * 2"
        :height="preset.height - template.borderInset * 2"
        fill="none"
        :stroke="template.borderColor"
        stroke-width="1.5"
        opacity="0.85"
      />
      <line
        :x1="template.borderInset"
        :y1="template.borderInset + 34"
        :x2="preset.width - template.borderInset"
        :y2="template.borderInset + 34"
        :stroke="template.borderColor"
        stroke-width="1"
        opacity="0.5"
      />
      <line
        :x1="template.borderInset"
        :y1="preset.height - template.borderInset - 34"
        :x2="preset.width - template.borderInset"
        :y2="preset.height - template.borderInset - 34"
        :stroke="template.borderColor"
        stroke-width="1"
        opacity="0.5"
      />
    </svg>

    <!-- 自定义透明边框图 -->
    <img
      v-if="template.customBorderImage"
      class="layer-border"
      :src="template.customBorderImage"
      alt="自定义边框"
      referrerpolicy="no-referrer"
    />

    <!-- 4. 安全区参考线（仅预览） -->
    <div
      v-if="meta.showSafeZoneGuide && !isExporting"
      class="safe-zone"
      :style="{
        left: px(template.paddingX),
        right: px(template.paddingX),
        top: px(template.paddingY),
        bottom: px(template.paddingY),
      }"
    >
      <span>{{ safeZoneLabel }}</span>
    </div>

    <!-- 5. 页眉 -->
    <header
      v-if="meta.showHeader"
      class="card-header"
      :style="{ borderColor: `${template.tableBorderColor}99`, color: template.mutedColor }"
    >
      <div class="header-left">
        <span class="header-dot" :style="{ backgroundColor: template.accentColor }" />
        <span class="header-name">{{ meta.headerLeft }}</span>
      </div>
      <span class="header-right">{{ meta.headerRight }}</span>
    </header>

    <!-- 6. 正文块 -->
    <div class="card-body">
      <template v-for="block in card.blocks" :key="block.id">
        <div v-if="block.type === 'h1'" class="block block-h1">
          <div class="h1-bar" :style="{ backgroundColor: template.accentColor }" />
          <h1 :style="{ fontSize: px(headingFontSize), color: template.textColor }" v-html="renderInline(block.content)" />
        </div>

        <h2
          v-else-if="block.type === 'h2'"
          class="block-h2"
          :style="{ fontSize: px(Math.round(15.5 * scale)), color: template.textColor }"
        >
          <span class="h2-mark" :style="{ color: template.accentColor }">§</span>
          <span v-html="renderInline(block.content)" />
        </h2>

        <h3
          v-else-if="block.type === 'h3'"
          class="block-h3"
          :style="{ fontSize: px(Math.round(13.5 * scale)), color: template.accentColor }"
          v-html="renderInline(block.content)"
        />

        <p
          v-else-if="block.type === 'paragraph'"
          class="block-p"
          :style="{ fontSize: px(Math.round(12.5 * scale)), color: template.textColor }"
          v-html="renderInline(block.content)"
        />

        <blockquote
          v-else-if="block.type === 'blockquote'"
          class="block-quote"
          :style="{
            backgroundColor: template.cardSurfaceBg,
            borderColor: template.tableBorderColor,
            fontSize: px(Math.round(11.8 * scale)),
            color: template.textColor,
          }"
        >
          <span class="quote-mark" :style="{ color: template.accentColor }">“</span>
          <span class="quote-text" v-html="renderInline(block.content)" />
        </blockquote>

        <figure v-else-if="block.type === 'image'" class="block-image">
          <div
            class="image-frame"
            :style="{ borderColor: template.tableBorderColor, backgroundColor: template.cardSurfaceBg }"
          >
            <img
              v-if="block.imageUrl && !failedImages.has(block.id)"
              :src="block.imageUrl"
              :alt="block.imageAlt || '卡片配图'"
              referrerpolicy="no-referrer"
              crossorigin="anonymous"
              @error="failedImages.add(block.id)"
            />
            <div v-else class="image-fallback">
              <span class="image-fallback-icon" :style="{ color: template.accentColor }">▣</span>
              <span :style="{ color: template.mutedColor }">{{ block.imageAlt || '图文插画预览占位' }}</span>
            </div>
          </div>
          <figcaption v-if="block.imageAlt" :style="{ color: template.mutedColor }">
            ▲ {{ block.imageAlt }}
          </figcaption>
        </figure>

        <div
          v-else-if="block.type === 'table' && block.table"
          class="block-table"
          :style="{ borderColor: template.tableBorderColor, backgroundColor: template.cardSurfaceBg }"
        >
          <table>
            <thead>
              <tr :style="{ backgroundColor: template.accentBgSoft, borderColor: template.tableBorderColor }">
                <th
                  v-for="(header, hIdx) in block.table.headers"
                  :key="hIdx"
                  :style="{
                    fontSize: px(Math.round(10.5 * scale)),
                    color: template.textColor,
                    borderColor: template.tableBorderColor,
                  }"
                  v-html="renderInline(header)"
                />
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, rIdx) in block.table.rows" :key="rIdx" :style="{ borderColor: template.tableBorderColor }">
                <td
                  v-for="(cell, cIdx) in row"
                  :key="cIdx"
                  :style="{
                    fontSize: px(Math.round(10.5 * scale)),
                    color: template.textColor,
                    borderColor: template.tableBorderColor,
                  }"
                  v-html="renderInline(cell)"
                />
              </tr>
            </tbody>
          </table>
        </div>

        <ul
          v-else-if="block.type === 'ul' || block.type === 'ol'"
          class="block-list"
          :style="{ fontSize: px(Math.round(12 * scale)) }"
        >
          <li v-for="(item, idx) in block.items || []" :key="idx">
            <span v-if="typeof item.checked === 'boolean'" class="list-icon" :style="{ color: item.checked ? template.accentColor : template.mutedColor }">
              <svg v-if="item.checked" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <path d="M7 12l3.5 3.5L17 8" />
              </svg>
              <svg v-else viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="3" y="3" width="18" height="18" rx="2" />
              </svg>
            </span>
            <span v-else-if="block.type === 'ol'" class="list-index" :style="{ color: template.accentColor }">
              {{ String(idx + 1).padStart(2, '0') }}.
            </span>
            <span v-else class="list-dot" :style="{ backgroundColor: template.accentColor }" />
            <span class="list-text" v-html="renderInline(item.text)" />
          </li>
        </ul>

        <div
          v-else-if="block.type === 'code'"
          class="block-code"
          :style="{ backgroundColor: template.cardSurfaceBg, borderColor: template.tableBorderColor }"
        >
          <div class="code-head">
            <span>{{ block.language || 'code' }}</span>
            <span>UTF-8</span>
          </div>
          <pre>{{ block.content }}</pre>
        </div>
      </template>
    </div>

    <!-- 7. 页脚与页码 -->
    <footer
      v-if="meta.showFooter || meta.showPageNumber"
      class="card-footer"
      :style="{ borderColor: `${template.tableBorderColor}99`, color: template.mutedColor }"
    >
      <div v-if="meta.showFooter" class="footer-left">
        <span class="footer-author" :style="{ color: template.textColor }">{{ meta.authorHandle }}</span>
        <template v-if="meta.footerNote">
          <span aria-hidden="true">·</span>
          <span class="footer-note">{{ meta.footerNote }}</span>
        </template>
      </div>
      <span v-else />
      <span v-if="meta.showPageNumber" class="footer-page" :style="{ color: template.textColor }">
        {{ String(card.index).padStart(2, '0') }}
        <span class="footer-total">/ {{ String(card.total).padStart(2, '0') }}</span>
      </span>
    </footer>
  </div>
</template>

<style scoped>
.card-canvas {
  position: relative;
  overflow: hidden;
  user-select: none;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  flex-shrink: 0;
}

.font-serif {
  font-family: 'Songti SC', 'Noto Serif SC', 'Source Han Serif SC', Georgia, serif;
}

.font-sans {
  font-family: var(--font-stack);
}

.font-mono {
  font-family: 'JetBrains Mono', 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
}

.layer-bg,
.layer-svg,
.layer-border {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.layer-bg {
  object-fit: cover;
  z-index: 0;
}

.layer-svg {
  z-index: 1;
}

.layer-border {
  object-fit: fill;
  z-index: 20;
}

.safe-zone {
  position: absolute;
  z-index: 30;
  border: 1px dashed color-mix(in srgb, var(--color-danger) 60%, transparent);
  pointer-events: none;
}

.safe-zone span {
  position: absolute;
  top: -16px;
  left: 0;
  font-size: 9px;
  font-family: monospace;
  color: var(--color-danger);
  background: color-mix(in srgb, var(--color-surface) 92%, transparent);
  padding: 0 4px;
  white-space: nowrap;
}

.card-header,
.card-footer {
  position: relative;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 10px;
  letter-spacing: 0.04em;
  flex-shrink: 0;
}

.card-header {
  padding-bottom: 8px;
  margin-bottom: 8px;
  border-bottom: 1px solid;
}

.card-footer {
  padding-top: 8px;
  margin-top: 8px;
  border-top: 1px solid;
}

.header-left,
.footer-left {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.header-dot {
  width: 6px;
  height: 6px;
  border-radius: 1px;
  flex-shrink: 0;
}

.header-name,
.footer-note {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}

.header-right,
.footer-page {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.header-right {
  opacity: 0.8;
}

.footer-author {
  font-weight: 600;
  white-space: nowrap;
}

.footer-page {
  font-size: 10.5px;
  font-weight: 500;
}

.footer-total {
  opacity: 0.45;
}

.card-body {
  position: relative;
  z-index: 10;
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  gap: 10px;
  overflow: hidden;
}

.block-h1 {
  padding: 4px 0 6px;
}

.h1-bar {
  width: 28px;
  height: 4px;
  border-radius: 1px;
  margin-bottom: 8px;
}

.block-h1 h1 {
  margin: 0;
  font-weight: 700;
  line-height: 1.28;
  letter-spacing: -0.02em;
  text-wrap: balance;
}

.block-h2 {
  margin: 0;
  padding: 4px 0 2px;
  font-weight: 700;
  line-height: 1.35;
  letter-spacing: -0.01em;
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.h2-mark {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 0.82em;
  font-weight: 600;
  flex-shrink: 0;
}

.block-h3 {
  margin: 0;
  padding-top: 2px;
  font-weight: 600;
  line-height: 1.4;
  letter-spacing: -0.01em;
}

.block-p {
  margin: 0;
  line-height: 1.62;
  font-weight: 400;
  word-break: break-word;
}

.block-quote {
  margin: 2px 0;
  padding: 10px;
  border: 1px solid;
  border-radius: 4px;
  line-height: 1.58;
}

.quote-mark {
  font-family: Georgia, serif;
  font-size: 16px;
  font-weight: 700;
  line-height: 1;
  margin-right: 4px;
}

.quote-text {
  opacity: 0.9;
}

.block-image {
  margin: 0;
}

.image-frame {
  position: relative;
  width: 100%;
  height: 126px;
  border: 1px solid;
  border-radius: 4px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}

.image-frame img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.image-fallback {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0 16px;
  text-align: center;
  font-size: 11px;
  font-weight: 500;
}

.image-fallback-icon {
  font-size: 18px;
  opacity: 0.55;
}

.block-image figcaption {
  margin-top: 4px;
  font-size: 10px;
  text-align: center;
  letter-spacing: 0.02em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.block-table {
  margin: 4px 0;
  border: 1px solid;
  border-radius: 4px;
  overflow: hidden;
}

.block-table table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
  font-family: var(--font-stack);
}

.block-table thead tr {
  border-bottom: 1px solid;
}

.block-table th {
  padding: 6px 8px;
  font-weight: 600;
  line-height: 1.25;
  border-right: 1px solid;
}

.block-table th:last-child,
.block-table td:last-child {
  border-right: none;
}

.block-table tbody tr {
  border-bottom: 1px solid;
}

.block-table tbody tr:last-child {
  border-bottom: none;
}

.block-table td {
  padding: 6px 8px;
  line-height: 1.35;
  font-variant-numeric: tabular-nums;
  border-right: 1px solid;
}

.block-list {
  margin: 2px 0;
  padding: 0;
  list-style: none;
  line-height: 1.55;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.block-list li {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.list-icon {
  display: inline-flex;
  margin-top: 2px;
  flex-shrink: 0;
}

.list-icon svg {
  opacity: 0.9;
}

.list-index {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 10.5px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  margin-top: 2px;
  flex-shrink: 0;
}

.list-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 1px;
  margin-top: 8px;
  flex-shrink: 0;
}

.list-text {
  flex: 1;
  word-break: break-word;
}

.block-code {
  margin: 4px 0;
  padding: 8px;
  border: 1px solid;
  border-radius: 4px;
  font-family: 'JetBrains Mono', Consolas, monospace;
}

.code-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 4px;
  margin-bottom: 4px;
  border-bottom: 1px solid currentColor;
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  opacity: 0.6;
}

.block-code pre {
  margin: 0;
  font-size: 10px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  overflow: hidden;
}

.card-canvas :deep(.md-link) {
  text-decoration: underline;
  text-underline-offset: 2px;
  font-weight: 500;
}

.card-canvas :deep(mark) {
  padding: 1px 4px;
  border-radius: 2px;
  font-weight: 500;
}

.card-canvas :deep(code) {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 0.88em;
  padding: 1px 5px;
  border-radius: 3px;
  border: 1px solid;
}

.card-canvas :deep(strong) {
  font-weight: 600;
}

.card-canvas :deep(em) {
  font-style: italic;
  opacity: 0.9;
}
</style>
