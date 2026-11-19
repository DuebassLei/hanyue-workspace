<script setup>
/**
 * 角色管理：角色 CRUD + 权限勾选 + 菜单绑定。
 *
 * 三件事刻意放在同一页，因为它们是一件事的三个面：给角色什么权限、这些权限在界面上
 * 体现为哪些菜单、这个角色有多少人。拆成三个页面会让人来回跳着猜。
 *
 * 保存走「全量覆盖」语义（PUT），不做逐项 diff：权限表是几十项的小集合，
 * 全量覆盖比合并更可预测，也避免「取消勾选但没删掉」这类幽灵权限。
 */
import { computed, onMounted, ref } from 'vue'
import {
  NButton,
  NCheckbox,
  NCheckboxGroup,
  NInput,
  NInputNumber,
  NModal,
  NSpin,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'
import { api } from '../api/client'
import { useRbacStore } from '../stores/rbac'

const message = useMessage()
const dialog = useDialog()
const rbac = useRbacStore()
// 菜单绑定是另一枚权限点：只有 rbac.role.manage 的人不该被迫加载菜单接口
const canBindMenus = computed(() => rbac.can('rbac.menu.manage'))

const roles = ref([])
const permissions = ref([])
const menus = ref([])
const loading = ref(false)
const saving = ref(false)
const selectedId = ref(null)
const draft = ref({ codes: [], menuIds: [] })

const showCreate = ref(false)
const createForm = ref({ roleKey: '', name: '', description: '', sort: 100 })

const selected = computed(() => roles.value.find((role) => role.id === selectedId.value) || null)

const permissionGroups = computed(() => {
  const groups = new Map()
  for (const item of permissions.value) {
    if (!groups.has(item.module)) groups.set(item.module, [])
    groups.get(item.module).push(item)
  }
  return [...groups.entries()].map(([module, items]) => ({ module, items }))
})

function flattenMenuItems(nodes, depth = 0) {
  return (nodes || []).flatMap((node) => [
    { id: node.id, label: node.label, permission: node.permission, visible: node.visible, isGroup: node.kind === 'group', depth },
    ...flattenMenuItems(node.children, depth + 1),
  ])
}

const menuGroups = computed(() =>
  menus.value.map((group) => ({
    id: group.id,
    label: group.label,
    items: flattenMenuItems(group.children, 1),
  })),
)

async function load() {
  loading.value = true
  try {
    const [roleList, permissionList, menuTree] = await Promise.all([
      api.get('/rbac/roles'),
      api.get('/rbac/permissions'),
      canBindMenus.value ? api.get('/rbac/menus') : Promise.resolve([]),
    ])
    roles.value = roleList
    permissions.value = permissionList
    menus.value = menuTree
    if (!selectedId.value && roleList.length) select(roleList[0])
  } catch (err) {
    message.error(err.message)
  } finally {
    loading.value = false
  }
}

function select(role) {
  selectedId.value = role.id
  draft.value = {
    codes: [...role.permissionCodes],
    menuIds: [...role.menuIds],
  }
}

function isBound(menuId) {
  return draft.value.menuIds.includes(menuId)
}

function toggleMenu(menuId, checked) {
  const set = new Set(draft.value.menuIds)
  if (checked) set.add(menuId)
  else set.delete(menuId)
  draft.value.menuIds = [...set]
}

function collectMenuIds(node, ids = new Set()) {
  ids.add(node.id)
  for (const child of node.children || []) collectMenuIds(child, ids)
  return ids
}

/** 勾选子项时自动补上它所属的分组，否则侧栏会因为这个分组没绑定而整块消失 */
function toggleGroupItem(groupId, checked) {
  const group = menus.value.find((item) => item.id === groupId)
  const ids = new Set(draft.value.menuIds)
  if (checked) {
    ids.add(groupId)
  } else {
    for (const id of collectMenuIds(group || { id: groupId })) ids.delete(id)
  }
  draft.value.menuIds = [...ids]
}

async function savePermissions() {
  if (!selected.value) return
  saving.value = true
  try {
    const updated = await api.put(`/rbac/roles/${selected.value.id}/permissions`, {
      codes: draft.value.codes,
    })
    replaceRole(updated)
    message.success('权限已保存，相关用户下次请求即生效')
  } catch (err) {
    message.error(err.message)
  } finally {
    saving.value = false
  }
}

async function saveMenus() {
  if (!selected.value) return
  saving.value = true
  try {
    const updated = await api.put(`/rbac/roles/${selected.value.id}/menus`, {
      menuIds: draft.value.menuIds,
    })
    replaceRole(updated)
    message.success('菜单绑定已保存')
  } catch (err) {
    message.error(err.message)
  } finally {
    saving.value = false
  }
}

function replaceRole(updated) {
  const index = roles.value.findIndex((role) => role.id === updated.id)
  if (index >= 0) roles.value.splice(index, 1, updated)
  if (selectedId.value === updated.id) {
    draft.value = { codes: [...updated.permissionCodes], menuIds: [...updated.menuIds] }
  }
}

async function createRole() {
  if (!createForm.value.roleKey || !createForm.value.name) {
    message.warning('标识与名称必填')
    return
  }
  try {
    const created = await api.post('/rbac/roles', createForm.value)
    roles.value.push(created)
    select(created)
    showCreate.value = false
    createForm.value = { roleKey: '', name: '', description: '', sort: 100 }
    message.success('角色已创建')
  } catch (err) {
    message.error(err.message)
  }
}

function removeRole(role) {
  dialog.warning({
    title: '删除角色',
    content: `删除「${role.name}」后不可恢复。内置角色与仍被用户使用的角色无法删除。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await api.del(`/rbac/roles/${role.id}`)
        roles.value = roles.value.filter((item) => item.id !== role.id)
        if (selectedId.value === role.id) {
          selectedId.value = null
          if (roles.value.length) select(roles.value[0])
        }
        message.success('已删除')
      } catch (err) {
        message.error(err.message)
      }
    },
  })
}

onMounted(load)
</script>

<template>
  <section class="rbac-page">
    <header class="page-head">
      <div>
        <h1>角色管理</h1>
        <p>角色决定「能看到哪些菜单、能调哪些接口」。权限改动对已登录用户立即生效。</p>
      </div>
      <n-button type="primary" @click="showCreate = true">新建角色</n-button>
    </header>

    <n-spin :show="loading">
      <div class="layout">
        <aside class="role-list">
          <button
            v-for="role in roles"
            :key="role.id"
            type="button"
            class="role-item"
            :class="{ active: role.id === selectedId }"
            @click="select(role)"
          >
            <span class="role-name">
              {{ role.name }}
              <n-tag v-if="role.isSuperadmin" size="small" type="warning">超管</n-tag>
              <n-tag v-else-if="role.isSystem" size="small">内置</n-tag>
            </span>
            <span class="role-meta">{{ role.roleKey }} · {{ role.userCount }} 人 · {{ role.permissionCodes.length }} 项权限</span>
          </button>
        </aside>

        <div v-if="selected" class="detail">
          <div class="detail-head">
            <div>
              <h2>{{ selected.name }}</h2>
              <p class="muted">{{ selected.description || '暂无描述' }}</p>
            </div>
            <n-button
              v-if="!selected.isSystem && !selected.isSuperadmin"
              tertiary
              type="error"
              @click="removeRole(selected)"
            >
              删除角色
            </n-button>
          </div>

          <n-alert v-if="selected.isSuperadmin" type="warning" :bordered="false">
            超级管理员拥有全部权限，不受下面勾选限制。要限制它，请改用其他角色。
          </n-alert>

          <section class="block">
            <div class="block-head">
              <h3>权限点</h3>
              <n-button size="small" type="primary" :loading="saving" @click="savePermissions">保存权限</n-button>
            </div>
            <div class="perm-groups">
              <div v-for="group in permissionGroups" :key="group.module" class="perm-group">
                <div class="perm-group-title">{{ group.module }}</div>
                <n-checkbox-group v-model:value="draft.codes">
                  <div class="perm-items">
                    <n-checkbox v-for="item in group.items" :key="item.code" :value="item.code">
                      {{ item.name }}
                      <code>{{ item.code }}</code>
                    </n-checkbox>
                  </div>
                </n-checkbox-group>
              </div>
            </div>
          </section>

          <section v-if="canBindMenus" class="block">
            <div class="block-head">
              <h3>菜单可见性</h3>
              <n-button size="small" type="primary" :loading="saving" @click="saveMenus">保存菜单</n-button>
            </div>
            <p class="muted">
              菜单是权限的投影：既要「绑定了这个菜单」，也要「有这个菜单要求的权限」才会显示。
              取消分组会同时取消它下面的子项。
            </p>
            <div class="menu-groups">
              <div v-for="group in menuGroups" :key="group.id" class="menu-group">
                <n-checkbox
                  :checked="isBound(group.id)"
                  @update:checked="(checked) => toggleGroupItem(group.id, checked)"
                >
                  <strong>{{ group.label }}</strong>
                </n-checkbox>
                <div class="menu-items">
                  <n-checkbox
                    v-for="item in group.items"
                    :key="item.id"
                    :checked="isBound(item.id)"
                    :style="{ marginLeft: `${(item.depth - 1) * 18}px` }"
                    @update:checked="(checked) =>
                      item.isGroup ? toggleGroupItem(item.id, checked) : toggleMenu(item.id, checked)"
                    "
                  >
                    <strong v-if="item.isGroup">{{ item.label }}</strong>
                    <template v-else>{{ item.label }}</template>
                    <n-tag v-if="!item.visible" size="small" type="info">已隐藏</n-tag>
                  </n-checkbox>
                </div>
              </div>
            </div>
          </section>
        </div>
      </div>
    </n-spin>

    <n-modal
      v-model:show="showCreate"
      preset="card"
      title="新建角色"
      style="width: 460px"
      :bordered="false"
    >
      <div class="form">
        <label>角色标识（英文，创建后不可改）</label>
        <n-input v-model:value="createForm.roleKey" placeholder="如 content_auditor" />
        <label>显示名称</label>
        <n-input v-model:value="createForm.name" placeholder="如 内容审核" />
        <label>描述</label>
        <n-input v-model:value="createForm.description" type="textarea" :rows="2" />
        <label>排序（小的在前）</label>
        <n-input-number v-model:value="createForm.sort" :min="0" :max="9999" />
      </div>
      <template #footer>
        <n-button @click="showCreate = false">取消</n-button>
        <n-button type="primary" style="margin-left: 8px" @click="createRole">创建</n-button>
      </template>
    </n-modal>
  </section>
</template>

<style scoped>
.rbac-page { padding: 24px 28px 40px; }
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.page-head h1 { margin: 0 0 6px; font-size: 20px; font-weight: 500; color: var(--color-text-primary); }
.page-head p { margin: 0; font-size: 13px; color: var(--color-text-muted); }
.layout { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 20px; align-items: start; }
.role-list { display: flex; flex-direction: column; gap: 6px; }
.role-item { text-align: left; padding: 10px 12px; border-radius: 8px; border: 1px solid var(--color-border); background: var(--color-surface); cursor: pointer; display: flex; flex-direction: column; gap: 4px; }
.role-item:hover { border-color: var(--color-border-strong); }
.role-item.active { border-color: var(--color-accent); background: var(--color-accent-soft); }
.role-name { display: flex; align-items: center; gap: 6px; font-size: 14px; color: var(--color-text-primary); }
.role-meta { font-size: 12px; color: var(--color-text-muted); }
.detail { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 12px; padding: 20px; display: flex; flex-direction: column; gap: 20px; }
.detail-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.detail-head h2 { margin: 0 0 4px; font-size: 16px; font-weight: 500; color: var(--color-text-primary); }
.block-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.block-head h3 { margin: 0; font-size: 14px; font-weight: 500; color: var(--color-text-primary); }
.muted { margin: 0; font-size: 12px; color: var(--color-text-muted); line-height: 1.6; }
.perm-groups { display: flex; flex-direction: column; gap: 14px; margin-top: 10px; }
.perm-group-title { font-size: 12px; color: var(--color-text-muted); margin-bottom: 6px; }
.perm-items { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 6px 16px; }
.perm-items code { margin-left: 4px; font-size: 11px; color: var(--color-text-tertiary); }
.menu-groups { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; margin-top: 12px; }
.menu-items { display: flex; flex-direction: column; gap: 4px; margin-top: 6px; padding-left: 22px; }
.form { display: flex; flex-direction: column; gap: 6px; }
.form label { font-size: 12px; color: var(--color-text-muted); margin-top: 8px; }
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
</style>
