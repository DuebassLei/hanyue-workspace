<script setup>
defineProps({ entries: { type: Array, default: () => [] } })
</script>

<template>
  <ol class="job-timeline">
    <li v-for="(entry, index) in entries" :key="`${entry.at}-${index}`">
      <span class="timeline-dot" aria-hidden="true" />
      <div><strong>{{ entry.status }}</strong><span>{{ entry.actor === 'user' ? '用户' : '系统' }} · {{ entry.at }}</span><p v-if="entry.note">{{ entry.note }}</p></div>
    </li>
  </ol>
</template>

<style scoped>
.job-timeline { margin:0; padding:0 0 0 8px; list-style:none; }
.job-timeline li { position:relative; display:flex; gap:12px; padding:0 0 18px 18px; border-left:1px solid var(--color-border); }
.job-timeline li:last-child { padding-bottom:0; border-left-color:transparent; }
.timeline-dot { position:absolute; left:-5px; top:2px; width:8px; height:8px; border-radius:50%; background:var(--color-accent); }
.job-timeline strong, .job-timeline span, .job-timeline p { display:block; }
.job-timeline strong { color:var(--color-text-primary); font-size:var(--font-sm); }
.job-timeline span { margin-top:3px; color:var(--color-text-muted); font-size:var(--font-xs); }
.job-timeline p { margin:5px 0 0; color:var(--color-text-secondary); font-size:var(--font-xs); }
</style>
