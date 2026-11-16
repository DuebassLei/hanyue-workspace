<script setup>
import { computed } from 'vue'
import { NAlert, NButton } from 'naive-ui'

/** 成片任务进度：五阶段步骤条 + 取消。task = useVideo().renderTask */
const props = defineProps({ task: { type: Object, default: null }, error: String })
const emit = defineEmits(['cancel', 'retry'])

const STAGES = [
  { key: 'images', label: '生成配图' },
  { key: 'voices', label: '合成旁白' },
  { key: 'compose', label: '拼接镜头' },
  { key: 'subtitles', label: '烧录字幕' },
  { key: 'bgm', label: '混入音乐' },
]

const stageIndex = computed(() => {
  const i = STAGES.findIndex((s) => s.key === props.task?.stage)
  return i
})

const failed = computed(() => Boolean(props.error))
</script>

<template>
  <div class="video-progress" :class="{ failed }">
    <ol class="stage-steps">
      <li v-for="(stage, i) in STAGES" :key="stage.key" :class="{ done: stageIndex > i, current: stageIndex === i && !failed, fail: failed && stageIndex === i }">
        <span class="dot">{{ failed && stageIndex === i ? '!' : stageIndex > i ? '✓' : i + 1 }}</span>
        <span class="stage-label">{{ stage.label }}</span>
      </li>
    </ol>
    <div class="progress-line"><i :style="{ width: `${task?.progress ?? 0}%` }" /></div>
    <div class="progress-foot">
      <span class="progress-text">{{ failed ? '成片失败' : `${task?.stage ? STAGES[stageIndex]?.label : '排队中'} · ${Math.round(task?.progress ?? 0)}%` }}</span>
      <n-button v-if="!failed" size="tiny" tertiary @click="emit('cancel')">取消任务</n-button>
      <n-button v-else size="tiny" secondary type="primary" @click="emit('retry')">重试成片</n-button>
    </div>
    <n-alert v-if="failed" type="error" :bordered="false" class="progress-error">{{ error }}</n-alert>
  </div>
</template>

<style scoped>
.video-progress { padding:12px 14px; border:1px solid #d8e0df; border-radius:10px; background:#f4f7f6; }.video-progress.failed { border-color:#e5c4c1; background:#faf3f2; }.stage-steps { display:flex; gap:4px; margin:0 0 10px; padding:0; list-style:none; }.stage-steps li { display:flex; align-items:center; gap:5px; flex:1; color:#9b9287; font-size:12px; }.stage-steps li.done, .stage-steps li.current { color:#39585f; }.stage-steps li.fail { color:#b8433c; }.dot { display:grid; place-items:center; width:18px; height:18px; border-radius:50%; background:#e2ded6; color:#7c7368; font-size:11px; }.done .dot { background:#41616a; color:#f5f3ee; }.current .dot { background:#fff; border:2px solid #41616a; }.fail .dot { background:#b8433c; color:#fff; }.stage-label { overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }.progress-line { height:5px; border-radius:3px; background:#e0ddd5; overflow:hidden; }.progress-line i { display:block; height:100%; background:linear-gradient(90deg,#41616a,#6c959b); border-radius:3px; transition:width .6s; }.failed .progress-line i { background:#c56a63; }.progress-foot { display:flex; align-items:center; justify-content:space-between; margin-top:8px; }.progress-text { color:#6d655b; font-size:12px; }.progress-error { margin-top:9px; }
</style>
