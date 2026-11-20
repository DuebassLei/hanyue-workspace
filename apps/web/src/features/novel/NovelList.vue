<script setup>
import { NButton } from 'naive-ui'

const props = defineProps({
  projects: { type: Array, default: () => [] },
  activeId: { type: String, default: '' },
})

const emit = defineEmits(['create', 'select', 'delete'])

function formatDate(value) {
  if (!value) return '刚刚'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}
</script>

<template>
  <section class="library">
    <header class="lib-head">
      <div>
        <h2 class="lib-title">作品</h2>
        <p class="lib-sub">共 {{ props.projects.length }} 部 · 每部独立保存章节、设定与人名对照</p>
      </div>
      <span v-if="props.projects.length" class="lib-hint">打开作品继续写</span>
    </header>

    <div v-if="!props.projects.length" class="lib-empty">
      <svg class="empty-glyph" viewBox="0 0 32 32" width="34" height="34" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true">
        <path d="M16 8.6C12.8 6.4 8.6 6 4.8 6.4v19c3.8-.4 8 0 11.2 2.2 3.2-2.2 7.4-2.6 11.2-2.2v-19c-3.8-.4-8 0-11.2 2.2Z" stroke-linejoin="round" />
        <path d="M16 8.6v19" />
      </svg>
      <h3>还没有作品</h3>
      <p>把一份你有权改写的原文粘进来，切章之后逐章改写、对照与续写。</p>
      <NButton type="primary" @click="emit('create')">从一份原文开始</NButton>
      <ol class="lib-steps">
        <li><span>1</span>粘贴原文</li>
        <li><span>2</span>自动切章</li>
        <li><span>3</span>逐章改写 / 续写</li>
      </ol>
    </div>

    <ul v-else class="lib-rows">
      <li
        v-for="item in props.projects"
        :key="item.id"
        class="lib-row"
        :class="{ 'is-active': item.id === props.activeId }"
      >
        <button type="button" class="row-main" @click="emit('select', item.id)">
          <span class="row-mark" aria-hidden="true">
            <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.4">
              <path d="M10 5.6C8 4.3 5.4 4.1 3 4.3v11c2.4-.2 5 0 7 1.3 2-1.3 4.6-1.5 7-1.3v-11c-2.4-.2-5 0-7 1.3Z" stroke-linejoin="round" />
              <path d="M10 5.6v11" />
            </svg>
          </span>
          <span class="row-text">
            <strong>{{ item.title || '未命名' }}</strong>
            <small>{{ item.chapterCount || 0 }} 章 · 更新于 {{ formatDate(item.updatedAt) }}</small>
          </span>
          <span class="row-arrow" aria-hidden="true">›</span>
        </button>
        <button type="button" class="row-del" @click="emit('delete', item.id)">删除</button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.library { border: 1px solid var(--paper-line-strong); border-radius: 10px; background: var(--paper-bg); overflow: hidden; }
.lib-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 16px 20px; background: var(--paper-header); border-bottom: 1px solid var(--paper-line); }
.lib-title { margin: 0; font-size: 15px; font-weight: 650; color: var(--paper-ink); }
.lib-sub { margin: 3px 0 0; font-size: 12px; color: var(--paper-ink-muted); }
.lib-hint { font-size: 11px; color: var(--paper-ink-muted); }

.lib-empty { display: flex; flex-direction: column; align-items: center; text-align: center; padding: 56px 24px 44px; }
.empty-glyph { color: var(--paper-accent); opacity: 0.9; }
.lib-empty h3 { margin: 14px 0 0; font-family: 'Source Han Serif SC', 'Noto Serif SC', 'Songti SC', Georgia, serif; font-size: 19px; font-weight: 650; color: var(--paper-ink); }
.lib-empty p { margin: 7px 0 18px; max-width: 380px; font-size: 13px; line-height: 1.7; color: var(--paper-ink-muted); }
.lib-steps { list-style: none; display: flex; gap: 22px; margin: 26px 0 0; padding: 16px 0 0; border-top: 1px dashed var(--paper-line-strong); font-size: 12px; color: var(--paper-ink-muted); }
.lib-steps li { display: flex; align-items: center; gap: 7px; }
.lib-steps span { display: grid; place-items: center; width: 18px; height: 18px; border-radius: 50%; border: 1px solid var(--paper-accent-line); color: var(--paper-accent); font-size: 10px; font-variant-numeric: tabular-nums; }

.lib-rows { list-style: none; margin: 0; padding: 0; }
.lib-row { display: flex; align-items: center; gap: 4px; padding-right: 14px; border-bottom: 1px solid var(--paper-line); }
.lib-row:last-child { border-bottom: 0; }
.lib-row.is-active { background: var(--paper-accent-tint); box-shadow: inset 3px 0 0 var(--paper-accent); }
.lib-row:hover { background: var(--paper-hover); }
.row-main { flex: 1; min-width: 0; display: flex; align-items: center; gap: 14px; padding: 14px 8px 14px 18px; border: 0; background: transparent; color: inherit; text-align: left; cursor: pointer; }
.row-mark { flex: 0 0 auto; display: grid; place-items: center; width: 34px; height: 34px; border: 1px solid var(--paper-line-strong); border-radius: 6px; background: var(--paper-surface); color: var(--paper-accent); }
.row-text { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.row-text strong { font-family: 'Source Han Serif SC', 'Noto Serif SC', 'Songti SC', Georgia, serif; font-size: 15px; font-weight: 650; color: var(--paper-ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row-text small { font-size: 11.5px; color: var(--paper-ink-muted); }
.row-arrow { margin-left: auto; color: var(--paper-ink-muted); font-size: 16px; }
.row-del { flex: 0 0 auto; padding: 4px 8px; border: 0; border-radius: 4px; background: transparent; color: var(--paper-ink-muted); font-size: 12px; cursor: pointer; }
.row-del:hover { color: var(--paper-danger); background: var(--paper-danger-soft); }

@media (max-width: 640px) {
  .lib-head { padding: 14px 16px; }
  .lib-steps { flex-direction: column; gap: 10px; align-items: flex-start; }
}
</style>
