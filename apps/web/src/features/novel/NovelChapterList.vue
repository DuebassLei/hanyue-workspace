<script setup>
defineProps({
  chapters: { type: Array, default: () => [] },
  activeId: { type: String, default: '' },
})

const emit = defineEmits(['select'])

const STATUS_LABEL = {
  pending: '待改写',
  drafted: '有草稿',
  accepted: '已接受',
}
</script>

<template>
  <section class="chapter-list">
    <h2 class="block-title">章节</h2>
    <p v-if="!chapters.length" class="empty-hint">还没有章节</p>
    <ul v-else class="chapter-items">
      <li v-for="chapter in chapters" :key="chapter.id">
        <button
          type="button"
          class="chapter-row"
          :class="{ 'is-active': chapter.id === activeId }"
          @click="emit('select', chapter.id)"
        >
          <span class="chapter-order">{{ (chapter.sortOrder ?? 0) + 1 }}</span>
          <span class="chapter-main">
            <strong>{{ chapter.title || '未命名' }}</strong>
            <small>
              {{ STATUS_LABEL[chapter.status] || chapter.status }}
              <span v-if="chapter.source === 'continuation'"> · 续写</span>
              <span v-if="chapter.stale" class="stale-mark"> · 可能过时</span>
            </small>
          </span>
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.chapter-list { min-width: 0; }
.block-title { margin: 0 0 10px; font-size: 15px; }
.empty-hint { margin: 0; color: #827a71; font-size: 13px; }
.chapter-items { list-style: none; margin: 0; padding: 0; }
.chapter-row { display: flex; align-items: flex-start; gap: 8px; width: 100%; padding: 8px 4px; border: 0; border-bottom: 1px solid #e1ddd6; background: transparent; text-align: left; cursor: pointer; color: inherit; }
.chapter-row.is-active { background: #f3efe7; }
.chapter-order { flex: none; width: 1.6em; color: #888077; font-size: 12px; }
.chapter-main { min-width: 0; }
.chapter-main strong, .chapter-main small { display: block; }
.chapter-main strong { font-size: 13px; overflow-wrap: anywhere; }
.chapter-main small { margin-top: 2px; color: #888077; font-size: 11px; }
.stale-mark { color: #9a6230; }
</style>
