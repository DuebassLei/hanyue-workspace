<script setup>
import { ref } from 'vue'
import { NButton } from 'naive-ui'

// 公众号 IP 白名单小部件：默认收起，展开后展示服务器出口 IP，提供重新检测与复制。
// 探测结果与加载/复制状态由页面级单次探测维护（多个账号行共享同一份），
// 本组件只做展示与事件转发，不自行请求。
defineProps({
  ip: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  copied: { type: Boolean, default: false },
})
defineEmits(['refresh', 'copy'])

const open = ref(false)
</script>

<template>
  <div class="account-egress" :class="{ 'is-open': open }">
    <button type="button" class="account-egress-toggle" @click="open = !open">
      <span>公众号 IP 白名单</span>
      <span class="account-egress-chevron" aria-hidden="true">{{ open ? '收起' : '展开' }}</span>
    </button>
    <div v-if="open" class="account-egress-body">
      <div class="account-egress-row">
        <code v-if="ip">{{ ip }}</code>
        <span v-else class="account-egress-muted">未检测到</span>
        <n-button size="tiny" quaternary :loading="loading" @click="$emit('refresh')">重新检测</n-button>
        <n-button v-if="ip" size="tiny" quaternary @click="$emit('copy')">
          {{ copied ? '已复制' : '复制' }}
        </n-button>
      </div>
      <span class="account-egress-hint">请填入公众号后台「设置与开发 → 基本配置 → IP 白名单」</span>
    </div>
  </div>
</template>

<style scoped>
.account-egress {
  margin-top: 2px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-raised);
  font-size: 11px;
  min-width: 0;
}

.account-egress-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 7px 9px;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
  text-align: left;
}

.account-egress-toggle > span:first-child {
  white-space: nowrap;
}

.account-egress-chevron {
  color: var(--color-text-muted);
}

.account-egress-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0 9px 8px;
}

.account-egress-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.account-egress code {
  padding: 2px 5px;
  border-radius: 3px;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-family: 'JetBrains Mono', Consolas, monospace;
  overflow-wrap: anywhere;
}

.account-egress-muted,
.account-egress-hint {
  color: var(--color-text-muted);
}
</style>
