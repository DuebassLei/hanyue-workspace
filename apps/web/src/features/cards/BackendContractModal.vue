<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NButton } from 'naive-ui'

const props = defineProps({
  open: { type: Boolean, default: false },
  engineMode: { type: String, default: 'frontend' },
  preset: { type: Object, required: true },
  template: { type: Object, required: true },
  meta: { type: Object, required: true },
  paginationMode: { type: String, default: 'smart-auto' },
  cards: { type: Array, default: () => [] },
  lastServerSyncAt: { type: String, default: null },
  isSyncing: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'change-engine-mode', 'trigger-server-test'])

const tab = ref('request')
const copied = ref('')
let copyTimer = null

const requestSample = computed(() => ({
  markdown: '## 示例章节标题\n\n用 ==高亮== 标记重点，用 **加粗** 强调结论。',
  presetId: props.preset.id,
  customWidth: props.preset.width,
  customHeight: props.preset.height,
  templateId: props.template.isCustom ? undefined : props.template.id,
  customTemplate: props.template.isCustom ? props.template : undefined,
  meta: props.meta,
  paginationMode: props.paginationMode,
}))

const responseShape = computed(() => ({
  status: 'ok',
  timestamp: new Date().toISOString(),
  renderSpec: {
    canvasWidth: props.preset.width,
    canvasHeight: props.preset.height,
    exportWidth: props.preset.width * props.preset.exportScale,
    exportHeight: props.preset.height * props.preset.exportScale,
    exportScale: props.preset.exportScale,
    ratioLabel: props.preset.ratioLabel,
    paginationMode: props.paginationMode,
  },
  template: props.template,
  meta: props.meta,
  totalCards: props.cards.length,
  cards: props.cards.slice(0, 1),
}))

const playwrightSnippet = computed(
  () => `// 后端无头渲染参考（Node 20 + Playwright）
// 1) 调用分页接口拿到 AST
const res = await fetch("/api/v1/cards/paginate", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Authorization: \`Bearer \${process.env.HANYUE_TOKEN}\`,
  },
  body: JSON.stringify(${JSON.stringify(requestSample.value, null, 2)}),
});
const { cards, renderSpec } = await res.json();

// 2) 用同一套 Vue 组件渲染每张卡片后截图
const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: renderSpec.canvasWidth, height: renderSpec.canvasHeight },
});
await page.goto("http://127.0.0.1:5173/cards");
await page.evaluate((payload) => window.__renderCardsForExport?.(payload), cards);
for (const card of cards) {
  const el = await page.$(\`[data-card-index="\${card.index}"]\`);
  await el.screenshot({
    path: \`card_\${String(card.index).padStart(2, "0")}.png\`,
    scale: "css",
  });
}
await browser.close();`,
)

const codeText = computed(() => {
  if (tab.value === 'request') return JSON.stringify(requestSample.value, null, 2)
  if (tab.value === 'response') return JSON.stringify(responseShape.value, null, 2)
  return playwrightSnippet.value
})

async function copyCode() {
  try {
    await navigator.clipboard.writeText(codeText.value)
    copied.value = tab.value
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => {
      copied.value = ''
    }, 1800)
  } catch {
    copied.value = ''
  }
}

function onKeydown(event) {
  if (event.key === 'Escape' && props.open) emit('close')
}

watch(
  () => props.open,
  (open) => {
    if (open) window.addEventListener('keydown', onKeydown)
    else window.removeEventListener('keydown', onKeydown)
  },
)

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  clearTimeout(copyTimer)
})
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="overlay" @click.self="emit('close')">
      <div class="modal" role="dialog" aria-modal="true" aria-label="前后端 API 契约">
        <header class="modal-head">
          <div class="title-block">
            <span class="eyebrow">MARKCARD ENGINE CONTRACT</span>
            <h2>前后端协同 · 渲染契约</h2>
            <p>
              同一份 Markdown 与模板配置，既可在浏览器内直出高清 PNG，也可提交给后端渲染队列批量出图。
            </p>
          </div>
          <button class="close-btn" aria-label="关闭" @click="emit('close')">×</button>
        </header>

        <div class="mode-switch">
          <button class="mode-btn" :class="{ 'is-active': engineMode === 'frontend' }" @click="emit('change-engine-mode', 'frontend')">
            <strong>纯前端引擎</strong>
            <span>html-to-image · 零延迟 · 单张 / ZIP 导出</span>
          </button>
          <button class="mode-btn" :class="{ 'is-active': engineMode === 'backend-api' }" @click="emit('change-engine-mode', 'backend-api')">
            <strong>后端 API 协同</strong>
            <span>POST /api/v1/cards/paginate · 可接入渲染队列</span>
          </button>
        </div>

        <div class="tabs">
          <button class="tab" :class="{ 'is-active': tab === 'request' }" @click="tab = 'request'">请求体 JSON</button>
          <button class="tab" :class="{ 'is-active': tab === 'response' }" @click="tab = 'response'">响应 AST 契约</button>
          <button class="tab" :class="{ 'is-active': tab === 'playwright' }" @click="tab = 'playwright'">无头渲染参考代码</button>
          <span class="spacer-flex" />
          <button class="copy-btn" @click="copyCode">{{ copied === tab ? '已复制 ✓' : '复制代码' }}</button>
        </div>

        <pre class="code-block">{{ codeText }}</pre>

        <footer class="modal-foot">
          <span class="sync">
            <span class="dot" :class="{ 'is-live': engineMode === 'backend-api' }" />
            {{
              engineMode === 'backend-api'
                ? isSyncing
                  ? '正在向后端同步 AST...'
                  : `后端同步于 ${lastServerSyncAt || '尚未触发'}`
                : '当前使用浏览器内分页引擎'
            }}
          </span>
          <div class="foot-actions">
            <n-button size="small" quaternary :disabled="isSyncing" @click="emit('trigger-server-test')">
              调用一次接口
            </n-button>
            <n-button size="small" type="primary" @click="emit('close')">关闭</n-button>
          </div>
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-5);
  background: var(--color-overlay);
  backdrop-filter: blur(4px);
}

.modal {
  width: min(880px, 100%);
  max-height: min(86vh, 820px);
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  overflow: hidden;
}

.modal-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-5);
  border-bottom: 1px solid var(--color-border);
}

.eyebrow {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 10px;
  letter-spacing: 0.16em;
  color: var(--color-accent);
}

.title-block h2 {
  margin: 4px 0 6px;
  font-size: var(--font-section);
}

.title-block p {
  margin: 0;
  font-size: var(--font-xs);
  line-height: 1.6;
  color: var(--color-text-secondary);
  max-width: 62ch;
}

.close-btn {
  background: none;
  border: none;
  font-size: 22px;
  line-height: 1;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 0 4px;
}

.close-btn:hover {
  color: var(--color-text-primary);
}

.mode-switch {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--color-border);
}

.mode-btn {
  display: flex;
  flex-direction: column;
  gap: 3px;
  text-align: left;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
  cursor: pointer;
}

.mode-btn strong {
  font-size: var(--font-sm);
}

.mode-btn span {
  font-size: 11px;
  color: var(--color-text-muted);
  font-family: 'JetBrains Mono', Consolas, monospace;
}

.mode-btn.is-active {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}

.tabs {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-5);
  border-bottom: 1px solid var(--color-border);
  overflow-x: auto;
}

.tab {
  padding: 5px 10px;
  font-size: var(--font-xs);
  font-weight: 500;
  color: var(--color-text-secondary);
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
  white-space: nowrap;
}

.tab.is-active {
  color: var(--color-text-primary);
  background: var(--color-surface-raised);
  border-color: var(--color-border);
}

.copy-btn {
  padding: 4px 8px;
  font-size: 11px;
  color: var(--color-accent);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-sm);
  cursor: pointer;
  white-space: nowrap;
}

.code-block {
  flex: 1;
  margin: 0;
  padding: var(--space-4) var(--space-5);
  overflow: auto;
  background: #0b0b0e;
  color: #e4e4e7;
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  line-height: 1.65;
  white-space: pre;
}

.modal-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-5);
  border-top: 1px solid var(--color-border);
  flex-wrap: wrap;
}

.sync {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
}

.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-text-muted);
}

.dot.is-live {
  background: var(--color-success);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-success) 28%, transparent);
}

.foot-actions {
  display: flex;
  gap: var(--space-2);
}

.spacer-flex {
  flex: 1;
}

@media (max-width: 640px) {
  .mode-switch {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
