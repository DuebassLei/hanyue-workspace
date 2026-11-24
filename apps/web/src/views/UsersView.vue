<script setup>
/**
 * 用户管理：建号、改昵称、分配角色、重置密码、停用/启用。
 *
 * 一个人可以同时拥有多个角色，权限取并集。这里不给「直接勾权限点」的入口——
 * 那样权限会散落在每个用户身上，半年后没人说得清某个用户为什么能进某个页面。
 *
 * 初始密码和重置后的密码只在对话框里出现一次，列表接口不回传。
 */
import { computed, onMounted, ref } from 'vue'
import {
  NButton,
  NCheckbox,
  NInput,
  NModal,
  NSpin,
  NTag,
  useMessage,
} from 'naive-ui'
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
const status = ref('active')

const drafts = ref({})
const nameDrafts = ref({})

const showCreate = ref(false)
const creating = ref(false)
const createForm = ref({ email: '', displayName: '', roleIds: [] })
const revealed = ref(null)

const memberRoleId = computed(() => roles.value.find((role) => role.roleKey === 'member')?.id || '')

const roleMeta = computed(() => {
  const map = new Map()
  for (const role of roles.value) map.set(role.id, role)
  return map
})

async function load() {
  loading.value = true
  try {
    const [userList, roleList] = await Promise.all([
      api.get(`/rbac/users?q=${encodeURIComponent(keyword.value)}&status=${status.value}`),
      api.get('/rbac/roles'),
    ])
    users.value = userList
    roles.value = roleList
    drafts.value = {}
    nameDrafts.value = {}
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

function nameOf(user) {
  return nameDrafts.value[user.id] ?? user.displayName
}

function isDirty(user) {
  const nameChanged = nameOf(user).trim() !== user.displayName
  const draft = drafts.value[user.id]
  const rolesChanged = Boolean(
    draft && (draft.length !== user.roleIds.length || draft.some((id) => !user.roleIds.includes(id))),
  )
  return nameChanged || rolesChanged
}

function replaceUser(updated) {
  const index = users.value.findIndex((item) => item.id === updated.id)
  if (index >= 0) users.value.splice(index, 1, updated)
  else users.value.unshift(updated)
}

async function save(user) {
  const nextName = nameOf(user).trim()
  if (!nextName) {
    message.warning('昵称不能为空')
    return
  }
  savingId.value = user.id
  try {
    let updated = user
    if (nextName !== user.displayName) {
      updated = await api.patch(`/rbac/users/${user.id}`, { displayName: nextName })
    }
    if (drafts.value[user.id]) {
      updated = await api.put(`/rbac/users/${user.id}/roles`, { roleIds: selectionOf(user) })
    }
    replaceUser(updated)
    delete drafts.value[user.id]
    delete nameDrafts.value[user.id]
    drafts.value = { ...drafts.value }
    nameDrafts.value = { ...nameDrafts.value }
    if (session.user?.id === user.id) await rbac.load()
    message.success('已保存')
  } catch (err) {
    message.error(err.message)
  } finally {
    savingId.value = ''
  }
}

async function toggleActive(user) {
  savingId.value = user.id
  try {
    const updated = await api.patch(`/rbac/users/${user.id}`, { isActive: !user.isActive })
    if (status.value !== 'all' && updated.isActive !== (status.value === 'active')) {
      users.value = users.value.filter((item) => item.id !== user.id)
    } else {
      replaceUser(updated)
    }
    message.success(updated.isActive ? '已启用' : '已停用')
  } catch (err) {
    message.error(err.message)
  } finally {
    savingId.value = ''
  }
}

async function resetPassword(user) {
  savingId.value = user.id
  try {
    const result = await api.post(`/rbac/users/${user.id}/reset-password`)
    replaceUser(result.user)
    revealed.value = {
      title: '新密码只显示这一次',
      email: result.user.email,
      password: result.initialPassword,
      note: '对方已登录的会话已全部失效，需要用这个新密码重新登录。',
    }
  } catch (err) {
    message.error(err.message)
  } finally {
    savingId.value = ''
  }
}

function openCreate() {
  createForm.value = {
    email: '',
    displayName: '',
    roleIds: memberRoleId.value ? [memberRoleId.value] : [],
  }
  showCreate.value = true
}

function toggleCreateRole(roleId, checked) {
  const current = new Set(createForm.value.roleIds)
  if (checked) current.add(roleId)
  else current.delete(roleId)
  createForm.value.roleIds = [...current]
}

async function submitCreate() {
  if (!createForm.value.roleIds.length) {
    message.warning('至少选择一个角色')
    return
  }
  creating.value = true
  try {
    const result = await api.post('/rbac/users', {
      email: createForm.value.email.trim(),
      displayName: createForm.value.displayName.trim() || null,
      roleIds: createForm.value.roleIds,
    })
    showCreate.value = false
    if (status.value !== 'disabled') replaceUser(result.user)
    revealed.value = {
      title: '账号已创建，密码只显示这一次',
      email: result.user.email,
      password: result.initialPassword,
      note: '请现在复制给对方。关闭后无法再查看。',
    }
  } catch (err) {
    message.error(err.message)
  } finally {
    creating.value = false
  }
}

async function copyPassword() {
  try {
    await navigator.clipboard.writeText(revealed.value.password)
    message.success('已复制')
  } catch {
    message.warning('复制失败，请手动选中密码')
  }
}

onMounted(load)
</script>

<template>
  <section class="page users-page">
    <header class="page-head">
      <div>
        <h1 class="page-title">用户管理</h1>
        <p class="page-sub">多个角色取权限并集。停用后对方的下一次请求会被拒绝；重置密码会让已登录的会话立即失效。不能停用自己，也不能停用最后一个启用中的超管。</p>
      </div>
      <n-button type="primary" @click="openCreate">新建用户</n-button>
    </header>

    <div class="toolbar">
      <div class="filters">
        <button
          v-for="item in [
            { key: 'active', label: '启用中' },
            { key: 'disabled', label: '已停用' },
            { key: 'all', label: '全部' },
          ]"
          :key="item.key"
          type="button"
          class="filter"
          :class="{ on: status === item.key }"
          @click="status = item.key; load()"
        >
          {{ item.label }}
        </button>
      </div>
      <div class="search">
        <n-input
          v-model:value="keyword"
          placeholder="搜索邮箱或昵称（跨全部状态）"
          clearable
          @keyup.enter="load"
        />
        <n-button @click="load">搜索</n-button>
      </div>
    </div>

    <n-spin :show="loading">
      <div class="list">
        <div v-for="user in users" :key="user.id" class="row" :class="{ off: !user.isActive }">
          <div class="identity">
            <div class="name">
              <n-input
                size="small"
                :value="nameOf(user)"
                @update:value="(value) => (nameDrafts[user.id] = value)"
              />
              <n-tag v-if="session.user?.id === user.id" size="small" type="info">我</n-tag>
              <n-tag v-if="!user.isActive" size="small" type="error">已停用</n-tag>
            </div>
            <div class="email">{{ user.email || '开发态账号，无邮箱' }}</div>
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
            <n-button
              size="small"
              :disabled="!user.email || session.user?.id === user.id"
              :title="session.user?.id === user.id ? '请到账号设置里修改自己的密码' : user.email ? '生成一次性新密码并踢下线' : '没有邮箱，不能设置密码'"
              @click="resetPassword(user)"
            >
              重置密码
            </n-button>
            <n-button
              size="small"
              :type="user.isActive ? 'default' : 'primary'"
              :disabled="session.user?.id === user.id"
              :title="session.user?.id === user.id ? '不能停用自己' : ''"
              @click="toggleActive(user)"
            >
              {{ user.isActive ? '停用' : '启用' }}
            </n-button>
          </div>
        </div>
        <div v-if="!users.length && !loading" class="empty">没有匹配的用户</div>
      </div>
    </n-spin>

    <n-modal v-model:show="showCreate" preset="card" title="新建用户" style="width: 460px">
      <div class="form">
        <label>
          邮箱
          <n-input v-model:value="createForm.email" placeholder="name@example.com" />
        </label>
        <label>
          昵称
          <n-input v-model:value="createForm.displayName" placeholder="不填则用邮箱前缀" />
        </label>
        <div>
          <div class="field-label">角色（至少选一个）</div>
          <div class="roles">
            <n-checkbox
              v-for="role in roles"
              :key="role.id"
              :checked="createForm.roleIds.includes(role.id)"
              @update:checked="(checked) => toggleCreateRole(role.id, checked)"
            >
              {{ role.name }}
            </n-checkbox>
          </div>
        </div>
        <div class="modal-actions">
          <n-button @click="showCreate = false">取消</n-button>
          <n-button type="primary" :loading="creating" @click="submitCreate">创建</n-button>
        </div>
      </div>
    </n-modal>

    <n-modal :show="Boolean(revealed)" preset="card" :title="revealed?.title" style="width: 420px" :mask-closable="false" @update:show="(open) => { if (!open) revealed = null }">
      <div v-if="revealed" class="form">
        <p class="note">{{ revealed.note }}</p>
        <div class="secret">
          <div class="email">{{ revealed.email }}</div>
          <code>{{ revealed.password }}</code>
        </div>
        <div class="modal-actions">
          <n-button @click="copyPassword">复制密码</n-button>
          <n-button type="primary" @click="revealed = null">我已抄下</n-button>
        </div>
      </div>
    </n-modal>
  </section>
</template>

<style scoped>
.page-head p { max-width: 640px; line-height: 1.5; }
.toolbar { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); flex-wrap: wrap; }
.filters { display: flex; gap: 4px; }
.filter { border: 1px solid var(--color-border); background: transparent; color: var(--color-text-muted); border-radius: 8px; padding: 4px 12px; font-size: 13px; cursor: pointer; }
.filter.on { color: var(--color-text-primary); border-color: var(--color-text-tertiary); }
.search { display: flex; gap: 8px; width: 360px; }
.list { border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); overflow: hidden; }
.row { display: grid; grid-template-columns: 220px minmax(0, 1fr) 230px; gap: var(--space-3); align-items: center; padding: var(--space-3); border-top: 1px solid var(--color-border); }
.row:first-child { border-top: none; }
.row.off { opacity: 0.72; }
.name { display: flex; align-items: center; gap: 6px; }
.email { font-size: 12px; color: var(--color-text-muted); margin-top: 4px; }
.roles { display: flex; flex-wrap: wrap; gap: 8px 18px; }
.code { font-size: 11px; color: var(--color-text-tertiary); margin-left: 4px; }
.actions { display: flex; justify-content: flex-end; gap: 6px; flex-wrap: wrap; }
.empty { padding: 40px; text-align: center; color: var(--color-text-muted); font-size: 13px; }
.form { display: flex; flex-direction: column; gap: 14px; }
.form label, .field-label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--color-text-muted); }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
.note { margin: 0; font-size: 13px; line-height: 1.5; color: var(--color-text-muted); }
.secret { padding: 12px; border-radius: 8px; background: var(--color-surface-muted); }
.secret code { display: block; margin-top: 6px; font-size: 18px; letter-spacing: 0.04em; color: var(--color-text-primary); }
@media (max-width: 900px) {
  .toolbar, .page-head { flex-direction: column; align-items: stretch; }
  .search { width: auto; }
  .row { grid-template-columns: 1fr; }
}
</style>
