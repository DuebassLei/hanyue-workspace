<script setup>
/**
 * 用户授权：给账号分配角色。
 *
 * 一个人可以同时拥有多个角色，权限取**并集**（任一角色给了就算有）。
 * 这里刻意不给「直接勾权限点」的入口——那样权限会散落在每个用户身上，
 * 半年后没人说得清某个用户为什么能进某个页面。
 */
import { computed, onMounted, ref } from 'vue'
import { NButton, NCheckbox, NInput, NSpin, NTag, useMessage } from 'naive-ui'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import { useRbacStore } from '../stores/rbac'

const message = useMessage()
const session = useSessionStore()
const rbac = useRbacStore()

const users = ref([])
const roles = ref([])
const loading = ref(false)
const savingId = ref('')
const keyword = ref('')

// 本地编辑态：{ userId: [roleId] }，保存后清空
const drafts = ref({})

const roleMeta = computed(() => {
  const map = new Map()
  for (const role of roles.value) map.set(role.id, role)
  return map
})

async function load() {
  loading.value = true
  try {
    const [userList, roleList] = await Promise.all([
      api.get(`/rbac/users?q=${encodeURIComponent(keyword.value)}`),
      api.get('/rbac/roles'),
    ])
    users.value = userList
    roles.value = roleList
    drafts.value = {}
  } catch (err) {
    message.error(err.message)
  } finally {
    loading.value = false
  }
}

function selectionOf(user) {
  return drafts.value[user.id] ?? user.roleIds
}

function setSelection(user, roleId, checked) {
  const current = new Set(selectionOf(user))
  if (checked) current.add(roleId)
  else current.delete(roleId)
  drafts.value = { ...drafts.value, [user.id]: [...current] }
}

function isDirty(user) {
  const draft = drafts.value[user.id]
  if (!draft) return false
  return draft.length !== user.roleIds.length || draft.some((id) => !user.roleIds.includes(id))
}

async function save(user) {
  savingId.value = user.id
  try {
    const updated = await api.put(`/rbac/users/${user.id}/roles`, {
      roleIds: selectionOf(user),
    })
    const index = users.value.findIndex((item) => item.id === updated.id)
    if (index >= 0) users.value.splice(index, 1, updated)
    delete drafts.value[user.id]
    drafts.value = { ...drafts.value }
    // 改的是自己时，侧栏和权限要立刻跟着变
    if (session.user?.id === user.id) await rbac.load()
    message.success('已保存')
  } catch (err) {
    message.error(err.message)
  } finally {
    savingId.value = ''
  }
}

onMounted(load)
</script>

<template>
  <section class="users-page">
    <header class="page-head">
      <div>
        <h1>用户授权</h1>
        <p>多个角色取权限并集。超级管理员数量不受限制，但不能把最后一个超管降权。</p>
      </div>
      <div class="search">
        <n-input
          v-model:value="keyword"
          placeholder="搜索邮箱或昵称"
          clearable
          @keyup.enter="load"
        />
        <n-button @click="load">搜索</n-button>
      </div>
    </header>

    <n-spin :show="loading">
      <div class="list">
        <div v-for="user in users" :key="user.id" class="row">
          <div class="identity">
            <div class="name">
              {{ user.displayName }}
              <n-tag v-if="session.user?.id === user.id" size="small" type="info">我</n-tag>
              <n-tag v-if="!user.isActive" size="small" type="error">已停用</n-tag>
            </div>
            <div class="email">{{ user.email || '开发态账号' }}</div>
          </div>

          <div class="roles">
            <n-checkbox
              v-for="role in roles"
              :key="role.id"
              :checked="selectionOf(user).includes(role.id)"
              @update:checked="(checked) => setSelection(user, role.id, checked)"
            >
              {{ role.name }}
              <span class="code">{{ role.roleKey }}</span>
            </n-checkbox>
          </div>

          <div class="actions">
            <n-button
              size="small"
              type="primary"
              :disabled="!isDirty(user)"
              :loading="savingId === user.id"
              @click="save(user)"
            >
              保存
            </n-button>
          </div>
        </div>
        <div v-if="!users.length && !loading" class="empty">没有匹配的用户</div>
      </div>
    </n-spin>
  </section>
</template>

<style scoped>
.users-page { padding: 24px 28px 40px; }
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.page-head h1 { margin: 0 0 6px; font-size: 20px; font-weight: 500; color: var(--color-text-primary); }
.page-head p { margin: 0; font-size: 13px; color: var(--color-text-muted); }
.search { display: flex; gap: 8px; width: 320px; }
.list { border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface); overflow: hidden; }
.row { display: grid; grid-template-columns: 220px minmax(0, 1fr) 90px; gap: 16px; align-items: center; padding: 14px; border-top: 1px solid var(--color-border); }
.row:first-child { border-top: none; }
.name { display: flex; align-items: center; gap: 6px; font-size: 14px; color: var(--color-text-primary); }
.email { font-size: 12px; color: var(--color-text-muted); margin-top: 2px; }
.roles { display: flex; flex-wrap: wrap; gap: 8px 18px; }
.code { font-size: 11px; color: var(--color-text-tertiary); margin-left: 4px; }
.actions { display: flex; justify-content: flex-end; }
.empty { padding: 40px; text-align: center; color: var(--color-text-muted); font-size: 13px; }
@media (max-width: 900px) { .row { grid-template-columns: 1fr; } }
</style>
