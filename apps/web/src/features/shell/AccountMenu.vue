<script setup>
/**
 * AccountMenu — 顶栏右上角的身份入口。
 *
 * 只做「我是谁 + 去哪 + 怎么退出」，资料编辑交给 AccountDrawer。
 * 退出不放在顶栏常驻文本上：这里先进二次确认，再调用 session.logout()。
 */
import { computed, ref } from 'vue'
import { NPopover } from 'naive-ui'
import { useSessionStore } from '../../stores/session'

const emit = defineEmits(['profile', 'settings'])

const session = useSessionStore()
const open = ref(false)
const confirming = ref(false)

const displayName = computed(() => session.user?.displayName || '未登录')
const initial = computed(() => (displayName.value.trim().slice(0, 1) || '未').slice(0, 1).toUpperCase())
const providerLabel = computed(() =>
  session.user?.authProvider === 'idp' ? '已接入身份' : '本地账号',
)
const subject = computed(() => session.user?.authSubject || '')

function onShowChange(value) {
  open.value = value
  // 关闭即复位：下次打开不能停在上一次的「确认退出」状态
  if (!value) confirming.value = false
}

function goProfile() {
  open.value = false
  emit('profile')
}

function goSettings() {
  open.value = false
  emit('settings')
}

function cancelLogout() {
  confirming.value = false
}

async function doLogout() {
  confirming.value = false
  open.value = false
  await session.logout()
}
</script>

<template>
  <n-popover
    trigger="click"
    placement="bottom-end"
    :show-arrow="false"
    :show="open"
    :style="{ width: '248px', padding: '0' }"
    @update:show="onShowChange"
  >
    <template #trigger>
      <button
        type="button"
        class="account-trigger"
        :class="{ 'is-open': open }"
        :aria-label="`账号菜单，当前身份 ${displayName}`"
        :aria-expanded="open"
        aria-haspopup="menu"
      >
        <span class="account-avatar" aria-hidden="true">{{ initial }}</span>
        <span class="account-name">{{ displayName }}</span>
        <svg
          class="account-chevron"
          :class="{ 'is-open': open }"
          viewBox="0 0 20 20"
          width="12"
          height="12"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          aria-hidden="true"
        >
          <path d="m6 8 4 4 4-4" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
    </template>

    <div class="account-panel" role="menu" aria-label="账号菜单">
      <div class="account-head">
        <span class="account-avatar is-lg" aria-hidden="true">{{ initial }}</span>
        <span class="account-head-meta">
          <span class="account-head-name">{{ displayName }}</span>
          <span class="account-head-sub" :title="subject">{{ providerLabel }}</span>
        </span>
      </div>

      <div v-if="!confirming" class="account-actions">
        <button type="button" class="account-item" role="menuitem" @click="goProfile">
          <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
            <circle cx="10" cy="7" r="2.8" />
            <path d="M4.8 16c.6-2.6 2.7-4 5.2-4s4.6 1.4 5.2 4" stroke-linecap="round" />
          </svg>
          账号信息
        </button>
        <button type="button" class="account-item" role="menuitem" @click="goSettings">
          <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
            <circle cx="10" cy="10" r="2.2" />
            <path
              d="M10 3.6v1.5M10 14.9v1.5M3.6 10h1.5M14.9 10h1.5M5.5 5.5l1 1M13.5 13.5l1 1M14.5 5.5l-1 1M6.5 13.5l-1 1"
              stroke-linecap="round"
            />
          </svg>
          设置
        </button>
        <div class="account-divider" role="separator" />
        <button type="button" class="account-item is-danger" role="menuitem" @click="confirming = true">
          <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
            <path d="M8 4H5.5A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8M12.5 7 16 10.5 12.5 14M7.2 10.5H16" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          退出登录
        </button>
      </div>

      <div v-else class="account-confirm">
        <p class="account-confirm-title">退出登录？</p>
        <p class="account-confirm-body">退出后需要点「重新进入」才能继续创作，当前页的未保存改动不会保留。</p>
        <div class="account-confirm-actions">
          <button type="button" class="account-btn" @click="cancelLogout">取消</button>
          <button type="button" class="account-btn is-danger" @click="doLogout">退出登录</button>
        </div>
      </div>
    </div>
  </n-popover>
</template>

<style scoped>
.account-trigger {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  max-width: 200px;
  margin-left: 4px;
  padding: 0 8px 0 4px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.account-trigger:hover,
.account-trigger.is-open {
  background: var(--color-surface-raised);
}

.account-trigger:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.account-avatar {
  flex: 0 0 auto;
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--color-accent);
  color: var(--color-on-accent);
  font-size: 12px;
  font-weight: 650;
  line-height: 1;
}

.account-avatar.is-lg {
  width: 34px;
  height: 34px;
  font-size: 15px;
}

.account-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--font-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}

.account-chevron {
  flex: 0 0 auto;
  color: var(--color-text-muted);
  transition: transform 0.16s ease;
}

.account-chevron.is-open {
  transform: rotate(180deg);
}

.account-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: var(--space-3);
}

.account-head-meta {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.account-head-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--font-body);
  font-weight: 650;
  color: var(--color-text-primary);
}

.account-head-sub {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.account-actions {
  display: flex;
  flex-direction: column;
  padding: 0 var(--space-2) var(--space-2);
}

.account-divider {
  height: 1px;
  margin: var(--space-1) 0;
  background: var(--color-border);
}

.account-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 34px;
  padding: 0 10px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  font-size: var(--font-sm);
  text-align: left;
  cursor: pointer;
}

.account-item:hover {
  background: var(--color-surface-raised);
  color: var(--color-text-primary);
}

.account-item:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}

.account-item.is-danger {
  color: var(--color-danger);
}

.account-confirm {
  padding: var(--space-3);
}

.account-confirm-title {
  font-size: var(--font-sm);
  font-weight: 650;
  color: var(--color-text-primary);
}

.account-confirm-body {
  margin-top: 4px;
  font-size: var(--font-xs);
  line-height: 1.5;
  color: var(--color-text-muted);
}

.account-confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.account-btn {
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  font-size: var(--font-xs);
  cursor: pointer;
}

.account-btn:hover {
  background: var(--color-surface-raised);
}

.account-btn.is-danger {
  border-color: color-mix(in srgb, var(--color-danger) 45%, transparent);
  color: var(--color-danger);
}

.account-btn.is-danger:hover {
  background: color-mix(in srgb, var(--color-danger) 10%, transparent);
}

@media (max-width: 767px) {
  .account-name {
    display: none;
  }

  .account-trigger {
    max-width: none;
    padding: 0 6px 0 4px;
  }
}
</style>
