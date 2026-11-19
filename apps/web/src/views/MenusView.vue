<script setup>
/**
 * 菜单管理。
 *
 * 这里是「菜单是权限的投影」的落地处：每个菜单项挂一个权限点，角色再决定绑定与否。
 * 两条硬规则在界面上要能看见：
 *  - 分组不挂权限点，权限挂在具体页面上
 *  - 有子菜单的分组不能删除（要先删或移走子项）
 *
 * `path` 由服务端根据父子关系重算，前端不填也不传——手动维护路径一定会和树结构脱节。
 */
import { computed, onMounted, ref } from 'vue'
import {
  NButton,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSpin,
  NSwitch,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'
import { api } from '../api/client'

const message = useMessage()
const dialog = useDialog()

const tree = ref([])
const permissions = ref([])
const loading = ref(false)
const saving = ref(false)

const showForm = ref(false)
const editing = ref(null)
const form = ref(emptyForm())

function emptyForm(parentId = null) {
  return {
    parentId,
    key: '',
    label: '',
    short: '',
    icon: '',
    kind: parentId ? 'page' : 'group',
    permission: null,
    sort: 100,
    visible: true,
  }
}

const permissionOptions = computed(() =>
  permissions.value.map((item) => ({
    label: `${item.module} · ${item.name}（${item.code}）`,
    value: item.code,
  })),
)

const groupOptions = computed(() => {
  const options = []
  for (const group of tree.value) {
    options.push({ label: group.label, value: group.id })
    // 分类分组（如「工作区 → 文章」）也可以作为页面的父级
    for (const child of group.children || []) {
      if (child.kind === 'group') options.push({ label: `　└ ${child.label}`, value: child.id })
    }
  }
  return options
})

/** 树摊平成带缩进的行，比嵌套 DOM 更好控制行内操作 */
const rows = computed(() => {
  const flatten = (nodes, depth = 0) =>
    nodes.flatMap((node) => [{ ...node, depth }, ...flatten(node.children || [], depth + 1)])
  return flatten(tree.value)
})

async function load() {
  loading.value = true
  try {
    const [menuTree, permissionList] = await Promise.all([
      api.get('/rbac/menus'),
      api.get('/rbac/permissions'),
    ])
    tree.value = menuTree
    permissions.value = permissionList
  } catch (err) {
    message.error(err.message)
  } finally {
    loading.value = false
  }
}

function openCreate(parent = null) {
  editing.value = null
  form.value = emptyForm(parent?.id || null)
  showForm.value = true
}

function openEdit(menu) {
  editing.value = menu
  form.value = {
    parentId: menu.parentId,
    key: menu.key,
    label: menu.label,
    short: menu.short,
    icon: menu.icon,
    kind: menu.kind,
    permission: menu.permission,
    sort: menu.sort,
    visible: menu.visible,
  }
  showForm.value = true
}

async function submit() {
  if (!form.value.label) {
    message.warning('名称必填')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      const { key, ...payload } = form.value
      await api.patch(`/rbac/menus/${editing.value.id}`, payload)
      message.success('已保存')
    } else {
      if (!form.value.key) {
        message.warning('标识必填（页面类型需与前端路由名一致）')
        return
      }
      await api.post('/rbac/menus', form.value)
      message.success('已添加')
    }
    showForm.value = false
    await load()
  } catch (err) {
    message.error(err.message)
  } finally {
    saving.value = false
  }
}

async function toggleVisible(menu, visible) {
  try {
    await api.patch(`/rbac/menus/${menu.id}`, { visible })
    menu.visible = visible
  } catch (err) {
    message.error(err.message)
  }
}

function removeMenu(menu) {
  dialog.warning({
    title: '删除菜单',
    content: `删除「${menu.label}」后，所有角色都不再显示它。有子菜单的分组不能直接删除。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await api.del(`/rbac/menus/${menu.id}`)
        message.success('已删除')
        await load()
      } catch (err) {
        message.error(err.message)
      }
    },
  })
}

onMounted(load)
</script>

<template>
  <section class="menus-page">
    <header class="page-head">
      <div>
        <h1>菜单管理</h1>
        <p>
          侧栏由这里的数据生成。改动立即影响所有用户；页面类型的「标识」必须等于前端路由名，
          否则点击不会跳转。
        </p>
      </div>
      <n-button type="primary" @click="openCreate()">新增分组</n-button>
    </header>

    <n-spin :show="loading">
      <div class="table">
        <div class="table-head">
          <span>名称</span>
          <span>标识 / 路径</span>
          <span>权限点</span>
          <span>排序</span>
          <span>显示</span>
          <span>操作</span>
        </div>
        <div v-for="row in rows" :key="row.id" class="table-row" :class="{ child: row.depth > 0 }">
          <span class="cell-name" :style="{ paddingLeft: `${row.depth * 18}px` }">
            <span v-if="row.depth > 0" class="indent">└</span>
            <strong v-if="row.kind === 'group'">{{ row.label }}</strong>
            <span v-else>{{ row.label }}</span>
            <n-tag v-if="row.kind === 'group'" size="small" type="success">分组</n-tag>
          </span>
          <span class="cell-key">
            <code>{{ row.key }}</code>
            <span class="muted">{{ row.path }}</span>
          </span>
          <span class="cell-perm">
            <code v-if="row.permission">{{ row.permission }}</code>
            <span v-else class="muted">登录可见</span>
          </span>
          <span>{{ row.sort }}</span>
          <span>
            <n-switch
              :value="row.visible"
              size="small"
              @update:value="(value) => toggleVisible(row, value)"
            />
          </span>
          <span class="cell-actions">
            <n-button v-if="row.kind === 'group'" size="tiny" @click="openCreate(row)">加子项</n-button>
            <n-button size="tiny" @click="openEdit(row)">编辑</n-button>
            <n-button size="tiny" type="error" tertiary @click="removeMenu(row)">删除</n-button>
          </span>
        </div>
      </div>
    </n-spin>

    <n-modal
      v-model:show="showForm"
      preset="card"
      :title="editing ? '编辑菜单' : '新增菜单'"
      style="width: 520px"
      :bordered="false"
    >
      <div class="form">
        <label>所属分组</label>
        <n-select
          v-model:value="form.parentId"
          :options="groupOptions"
          clearable
          placeholder="留空即顶级分组（分组最多两级）"
        />
        <label>类型</label>
        <n-select
          v-model:value="form.kind"
          :options="[
            { label: '分组（侧栏标题）', value: 'group' },
            { label: '页面（对应一个路由）', value: 'page' },
            { label: '外链', value: 'link' },
          ]"
        />
        <label>标识（页面类型 = 前端路由 name）</label>
        <n-input v-model:value="form.key" :disabled="Boolean(editing)" placeholder="如 overview" />
        <label>名称</label>
        <n-input v-model:value="form.label" placeholder="如 创作总览" />
        <label>简称（折叠态/移动端）</label>
        <n-input v-model:value="form.short" placeholder="如 总览" />
        <label>图标键（前端图标字典，可留空）</label>
        <n-input v-model:value="form.icon" placeholder="如 overview" />
        <label>所需权限点</label>
        <n-select
          v-model:value="form.permission"
          :options="permissionOptions"
          clearable
          filterable
          placeholder="留空表示登录即可见"
          :disabled="form.kind === 'group'"
        />
        <label>排序（小的在前）</label>
        <n-input-number v-model:value="form.sort" :min="0" :max="9999" />
      </div>
      <template #footer>
        <n-button @click="showForm = false">取消</n-button>
        <n-button type="primary" :loading="saving" style="margin-left: 8px" @click="submit">
          保存
        </n-button>
      </template>
    </n-modal>
  </section>
</template>

<style scoped>
.menus-page { padding: 24px 28px 40px; }
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.page-head h1 { margin: 0 0 6px; font-size: 20px; font-weight: 500; color: var(--color-text-primary); }
.page-head p { margin: 0; font-size: 13px; color: var(--color-text-muted); max-width: 620px; line-height: 1.6; }
.table { border: 1px solid var(--color-border); border-radius: 12px; overflow: hidden; background: var(--color-surface); }
.table-head,
.table-row {
  display: grid;
  grid-template-columns: 1.4fr 1.5fr 1.4fr 70px 70px 180px;
  gap: 12px;
  align-items: center;
  padding: 10px 14px;
  font-size: 13px;
  color: var(--color-text-secondary);
}
.table-head { background: var(--color-surface-muted); color: var(--color-text-muted); font-size: 12px; }
.table-row { border-top: 1px solid var(--color-border); }
.table-row.child { background: var(--color-surface-raised); }
.cell-name { display: flex; align-items: center; gap: 6px; }
.indent { color: var(--color-text-tertiary); }
.cell-key { display: flex; flex-direction: column; gap: 2px; }
.cell-key code, .cell-perm code { font-size: 12px; color: var(--color-text-secondary); }
.cell-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.muted { font-size: 11px; color: var(--color-text-tertiary); }
.form { display: flex; flex-direction: column; gap: 6px; }
.form label { font-size: 12px; color: var(--color-text-muted); margin-top: 8px; }
@media (max-width: 980px) {
  .table-head { display: none; }
  .table-row { grid-template-columns: 1fr; gap: 6px; }
}
</style>
