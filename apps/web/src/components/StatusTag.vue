<script setup>
import { computed } from 'vue'

const props = defineProps({
  status: { type: String, required: true },
})

const MAP = {
  succeeded: { text: '成功', cls: 'tag-success' },
  connected: { text: '已连接', cls: 'tag-success' },
  active: { text: '运行中', cls: 'tag-accent' },
  running: { text: '执行中', cls: 'tag-accent' },
  publishing: { text: '发布中', cls: 'tag-accent' },
  queued: { text: '排队中', cls: 'tag-info' },
  approved: { text: '已批准', cls: 'tag-info' },
  pending_review: { text: '待审核', cls: 'tag-warning' },
  pending_authorization: { text: '待授权', cls: 'tag-warning' },
  expiring: { text: '即将过期', cls: 'tag-warning' },
  needs_attention: { text: '需核实', cls: 'tag-warning' },
  awaiting_approval: { text: '待审批', cls: 'tag-warning' },
  paused: { text: '已暂停', cls: 'tag-warning' },
  stopped_budget: { text: '超出预算已停止', cls: 'tag-warning' },
  reauthorization_required: { text: '需重新授权', cls: 'tag-danger' },
  failed: { text: '失败', cls: 'tag-danger' },
  rejected: { text: '已拒绝', cls: 'tag-danger' },
  cancelled: { text: '已取消', cls: 'tag-muted' },
  unbound: { text: '已解绑', cls: 'tag-muted' },
  draft: { text: '草稿', cls: 'tag-muted' },
  idea: { text: '灵感', cls: 'tag-muted' },
  planned: { text: '已规划', cls: 'tag-info' },
  idle: { text: '待运行', cls: 'tag-muted' },
  stopped_by_user: { text: '已由用户终止', cls: 'tag-muted' },
  missing_credential: { text: '缺少凭据', cls: 'tag-danger' },
  untested: { text: '未测试', cls: 'tag-warning' },
}

const info = computed(() => MAP[props.status] || { text: props.status, cls: 'tag-muted' })
</script>

<template>
  <span class="tag" :class="info.cls">{{ info.text }}</span>
</template>
