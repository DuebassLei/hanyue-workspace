<script setup>
import ShotCard from './ShotCard.vue'

const props = defineProps({ shots: { type: Array, default: () => [] }, selectedShotId: { type: String, default: '' } })
const emit = defineEmits(['select', 'move', 'delete', 'preview'])

function move(index, delta) {
  const target = index + delta
  if (target < 0 || target >= props.shots.length) return
  const ids = props.shots.map((shot) => shot.id)
  ;[ids[index], ids[target]] = [ids[target], ids[index]]
  emit('move', ids)
}
</script>

<template>
  <section class="shot-list">
    <div class="list-head">
      <div>
        <span class="eyebrow">SHOT BOARD</span>
        <h2>{{ props.shots.length ? `${props.shots.length} 个镜头` : '镜头编排' }}</h2>
      </div>
      <span class="list-note">拖动顺序，先看节奏再看细节</span>
    </div>
    <div v-if="!props.shots.length" class="list-empty">
      <div class="empty-mark">□</div>
      <strong>这个场景还没有镜头</strong>
      <p>从一个简单的构图开始，后面再补对白和正式画面。</p>
    </div>
    <div v-else class="cards">
      <ShotCard
        v-for="(shot, index) in props.shots"
        :key="shot.id"
        :shot="shot"
        :index="index"
        :active="shot.id === props.selectedShotId"
        @select="emit('select', $event)"
        @preview="emit('preview', $event)"
        @move-up="move(index, -1)"
        @move-down="move(index, 1)"
        @delete="emit('delete', $event)"
      />
    </div>
  </section>
</template>

<style scoped>
.shot-list { padding:24px clamp(16px,3vw,38px); background:var(--paper-sunken); min-height:560px; }
.list-head { display:flex; justify-content:space-between; align-items:flex-end; gap:12px; margin-bottom:18px; }.eyebrow { color:var(--paper-ink-muted); font-size:10px; letter-spacing:1.4px; }.list-head h2 { margin:4px 0 0; font-size:20px; color:var(--paper-ink); }.list-note { color:var(--paper-ink-muted); font-size:11px; }
.cards { display:flex; flex-direction:column; gap:10px; }.list-empty { max-width:340px; margin:90px auto; text-align:center; color:var(--paper-ink-muted); }.empty-mark { font-size:40px; color:var(--paper-ink-muted); }.list-empty strong { display:block; margin:12px 0 5px; color:var(--paper-ink); }.list-empty p { margin:0; font-size:13px; line-height:1.6; }
@media(max-width:720px) { .shot-list { padding:18px 12px; }.list-note { display:none; } }
</style>
