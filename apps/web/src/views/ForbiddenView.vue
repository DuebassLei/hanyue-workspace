<script setup>
/**
 * 403 页。
 *
 * 存在的意义：权限被调整后旧链接仍然可用，直接白屏或偷偷跳首页会让人以为「坏了」。
 * 明确告诉用户「没权限、缺哪个权限、能去哪」，比任何兜底跳转都好。
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton } from 'naive-ui'
import { useRbacStore } from '../stores/rbac'

const route = useRoute()
const router = useRouter()
const rbac = useRbacStore()

const missing = computed(() => String(route.query.need || ''))
const homeName = computed(() => rbac.firstNavName || 'overview')

function goHome() {
  router.replace({ name: homeName.value })
}
</script>

<template>
  <section class="forbidden">
    <div class="card">
      <div class="code">403</div>
      <h1>这个页面你还没有权限</h1>
      <p v-if="missing">
        需要权限点 <code>{{ missing }}</code>。请让管理员在「角色管理」里给你的角色补上，
        或在「用户管理」里把你加到合适的角色。
      </p>
      <p v-else>当前账号没有访问该页面的权限。</p>
      <div class="actions">
        <n-button type="primary" @click="goHome">回到可访问的页面</n-button>
      </div>
      <p class="hint">
        你的角色：{{ rbac.roleKeys.length ? rbac.roleKeys.join('、') : '未分配角色' }}
      </p>
    </div>
  </section>
</template>

<style scoped>
.forbidden { display: grid; place-items: center; min-height: 60vh; padding: 40px 24px; }
.card { max-width: 520px; text-align: center; }
.code { font-size: 44px; font-weight: 500; color: var(--color-text-tertiary); letter-spacing: 2px; }
h1 { margin: 8px 0 12px; font-size: 18px; font-weight: 500; color: var(--color-text-primary); }
p { margin: 0 0 10px; font-size: 13px; line-height: 1.7; color: var(--color-text-muted); }
code { font-size: 12px; color: var(--color-text-secondary); }
.actions { margin: 18px 0 10px; }
.hint { font-size: 12px; color: var(--color-text-tertiary); }
</style>
