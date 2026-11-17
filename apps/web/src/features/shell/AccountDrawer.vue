<script setup>
/**
 * AccountDrawer — 账号信息面板。
 *
 * 顶栏只放入口，具体资料在这里看与改：显示名、时区可改；
 * 身份来源（subject / 提供方 / 创建时间）只读，因为它在正式环境由 IdP 断言决定。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { NButton, NInput, NSelect } from 'naive-ui'
import { api } from '../../api/client'
import { useSessionStore } from '../../stores/session'
import { toast } from '../../ui/feedback'

const props = defineProps({
  open: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const session = useSessionStore()

const form = reactive({ displayName: '', timezone: '' })
const saving = ref(false)
const formError = ref('')
const workspaceName = ref('')
const workspaceError = ref('')
// 修改密码：成功后服务端会作废旧令牌并下发新令牌，session.changePassword 已接管
const pwd = reactive({ current: '', next: '', confirm: '' })
const pwdBusy = ref(false)
const pwdError = ref('')

const TIMEZONES = [
  { label: 'Asia/Shanghai（北京时间）', value: 'Asia/Shanghai' },
  { label: 'Asia/Hong_Kong', value: 'Asia/Hong_Kong' },
  { label: 'Asia/Taipei', value: 'Asia/Taipei' },
  { label: 'Asia/Tokyo', value: 'Asia/Tokyo' },
  { label: 'Asia/Singapore', value: 'Asia/Singapore' },
  { label: 'Europe/London', value: 'Europe/London' },
  { label: 'Europe/Berlin', value: 'Europe/Berlin' },
  { label: 'America/New_York', value: 'America/New_York' },
  { label: 'America/Los_Angeles', value: 'America/Los_Angeles' },
  { label: 'UTC', value: 'UTC' },
]

const displayName = computed(() => session.user?.displayName || '未登录')
const initial = computed(() =>
  (displayName.value.trim().slice(0, 1) || '未').slice(0, 1).toUpperCase(),
)
const providerLabel = computed(() => {
  const provider = session.user?.authProvider
  if (provider === 'password') return '邮箱密码'
  if (provider === 'idp') return '正式身份提供者（IdP）'
  return '本地开发会话'
})
const providerHint = computed(() => {
  const provider = session.user?.authProvider
  if (provider === 'password') {
    return '邮箱与密码由本服务保管；修改密码后其它设备上的登录会立即失效。'
  }
  if (provider === 'idp') return '由身份提供者签发，账号信息以 IdP 为准。'
  return '开发态按 subject 换取的本地账号；在登录页注册邮箱账号即可转为正式账号。'
})
const timezoneOptions = computed(() => {
  const options = [...TIMEZONES]
  const current = session.user?.timezone
  if (current && !options.some((item) => item.value === current)) {
    options.unshift({ label: current, value: current })
  }
  return options
})
const createdAtText = computed(() => {
  const raw = session.user?.createdAt
  if (!raw) return '—'
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
})
const dirty = computed(
  () =>
    form.displayName.trim() !== (session.user?.displayName || '') ||
    form.timezone !== (session.user?.timezone || ''),
)
const minPasswordLength = computed(() => session.authConfig?.passwordMinLength || 8)

function resetForm() {
  form.displayName = session.user?.displayName || ''
  form.timezone = session.user?.timezone || 'Asia/Shanghai'
  formError.value = ''
}

async function loadWorkspace() {
  workspaceError.value = ''
  try {
    const rows = await api.get('/workspaces')
    const current = rows.find((row) => row.id === session.workspaceId) || rows[0]
    workspaceName.value = current?.name || ''
    if (!current?.name) workspaceError.value = '未找到默认工作区'
  } catch (err) {
    workspaceError.value = `工作区读取失败：${err.message}`
  }
}

async function save() {
  if (saving.value) return
  const name = form.displayName.trim()
  if (!name) {
    formError.value = '显示名不能为空'
    return
  }
  formError.value = ''
  saving.value = true
  try {
    const user = await api.patch('/auth/me', {
      displayName: name,
      timezone: form.timezone,
    })
    session.applyUser(user)
    resetForm()
    toast('资料已保存')
  } catch (err) {
    // 不静默回滚：保留用户输入，把失败原因写在字段下方
    formError.value = err.message || '保存失败，请重试'
  } finally {
    saving.value = false
  }
}

function resetPasswordForm() {
  pwd.current = ''
  pwd.next = ''
  pwd.confirm = ''
  pwdError.value = ''
}

async function submitPassword() {
  if (pwdBusy.value) return
  if (!pwd.current) {
    pwdError.value = '请输入当前密码'
    return
  }
  if (pwd.next.length < minPasswordLength.value) {
    pwdError.value = `新密码至少 ${minPasswordLength.value} 位`
    return
  }
  if (pwd.next !== pwd.confirm) {
    pwdError.value = '两次输入的新密码不一致'
    return
  }
  if (pwd.next === pwd.current) {
    pwdError.value = '新密码不能与当前密码相同'
    return
  }
  pwdError.value = ''
  pwdBusy.value = true
  try {
    // 服务端会作废旧令牌并返回新令牌，session 内部已替换，不会把自己踢下线
    await session.changePassword(pwd.current, pwd.next)
    resetPasswordForm()
    toast('密码已更新，其它设备需重新登录')
  } catch (err) {
    pwdError.value = err.message || '修改失败，请重试'
  } finally {
    pwdBusy.value = false
  }
}

function onKeydown(event) {
  if (event.key === 'Escape' && props.open) emit('close')
}

watch(
  () => props.open,
  (value) => {
    if (!value) return
    resetForm()
    resetPasswordForm()
    loadWorkspace()
  },
  { immediate: true },
)

watch(
  () => session.user?.displayName,
  () => {
    if (!props.open) return
    resetForm()
  },
)

watch(
  () => props.open,
  (value) => {
    if (typeof document === 'undefined') return
    document.body.style.overflow = value ? 'hidden' : ''
    // 只有打开时才接键盘监听，避免常驻劫持 Esc
    if (value) document.addEventListener('keydown', onKeydown)
    else document.removeEventListener('keydown', onKeydown)
  },
)

// 挂载即打开（页面刷新后停留在上次的展开态）也要把监听接上
onMounted(() => {
  if (props.open && typeof document !== 'undefined') {
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', onKeydown)
  }
})

onBeforeUnmount(() => {
  if (typeof document === 'undefined') return
  document.body.style.overflow = ''
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="drawer-overlay"
      @click.self="emit('close')"
      @keydown="onKeydown"
    >
      <aside class="drawer" role="dialog" aria-modal="true" aria-label="账号信息">
        <header class="drawer-head">
          <h2 class="drawer-title">账号信息</h2>
          <button type="button" class="drawer-close" aria-label="关闭" @click="emit('close')">✕</button>
        </header>

        <div class="drawer-body">
          <div class="identity">
            <span class="identity-avatar" aria-hidden="true">{{ initial }}</span>
            <div class="identity-meta">
              <span class="identity-name">{{ displayName }}</span>
              <span class="identity-sub">{{ providerLabel }}</span>
            </div>
          </div>

          <section class="block">
            <h3 class="block-title">可编辑</h3>
            <div class="field">
              <label class="field-label" for="account-name">显示名</label>
              <n-input
                id="account-name"
                v-model:value="form.displayName"
                :maxlength="120"
                placeholder="请输入显示名"
                @keyup.enter="save"
              />
              <p class="field-hint">用于顶栏、稿件署名与导出的作者信息。</p>
            </div>
            <div class="field">
              <label class="field-label" for="account-tz">时区</label>
              <n-select
                id="account-tz"
                v-model:value="form.timezone"
                :options="timezoneOptions"
                filterable
                tag
                placeholder="选择或输入时区"
              />
              <p class="field-hint">用于「内容日历」的排期时间与发布任务的时间显示。</p>
            </div>
            <p v-if="formError" class="field-error" role="alert">{{ formError }}</p>
          </section>

          <section class="block">
            <h3 class="block-title">身份</h3>
            <dl class="facts">
              <div class="fact">
                <dt>身份标识</dt>
                <dd class="mono">{{ session.user?.authSubject || '—' }}</dd>
              </div>
              <div class="fact">
                <dt>登录方式</dt>
                <dd>{{ providerLabel }}</dd>
              </div>
              <div class="fact">
                <dt>创建时间</dt>
                <dd>{{ createdAtText }}</dd>
              </div>
            </dl>
            <p class="field-hint">{{ providerHint }}</p>
          </section>

          <section v-if="session.user?.hasPassword" class="block">
            <h3 class="block-title">修改密码</h3>
            <div class="field">
              <label class="field-label" for="pwd-current">当前密码</label>
              <n-input
                id="pwd-current"
                v-model:value="pwd.current"
                type="password"
                show-password-on="click"
                :input-props="{ autocomplete: 'current-password' }"
              />
            </div>
            <div class="field">
              <label class="field-label" for="pwd-next">新密码</label>
              <n-input
                id="pwd-next"
                v-model:value="pwd.next"
                type="password"
                show-password-on="click"
                :placeholder="`至少 ${minPasswordLength} 位`"
                :input-props="{ autocomplete: 'new-password' }"
              />
            </div>
            <div class="field">
              <label class="field-label" for="pwd-confirm">确认新密码</label>
              <n-input
                id="pwd-confirm"
                v-model:value="pwd.confirm"
                type="password"
                show-password-on="click"
                :input-props="{ autocomplete: 'new-password' }"
                @keyup.enter="submitPassword"
              />
              <p class="field-hint">修改后，其它设备上的登录会立即失效，需要重新登录。</p>
            </div>
            <p v-if="pwdError" class="field-error" role="alert">{{ pwdError }}</p>
            <div class="pwd-actions">
              <n-button size="small" type="primary" :loading="pwdBusy" @click="submitPassword">
                更新密码
              </n-button>
            </div>
          </section>

          <section class="block">
            <h3 class="block-title">工作区</h3>
            <p v-if="workspaceError" class="field-error">{{ workspaceError }}</p>
            <p v-else class="block-value">{{ workspaceName || '读取中…' }}</p>
            <p class="field-hint">稿件、素材与发布任务都归属于当前工作区。</p>
          </section>
        </div>

        <footer class="drawer-foot">
          <button type="button" class="btn-ghost" @click="emit('close')">关闭</button>
          <n-button type="primary" :loading="saving" :disabled="!dirty" @click="save">
            保存修改
          </n-button>
        </footer>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.drawer-overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  display: flex;
  justify-content: flex-end;
  background: var(--color-overlay);
}

.drawer {
  display: flex;
  flex-direction: column;
  width: min(420px, 100vw);
  height: 100%;
  background: var(--color-surface);
  box-shadow: var(--shadow-lg);
}

.drawer-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--color-border);
}

.drawer-title {
  flex: 1;
  font-size: var(--font-section);
  font-weight: 650;
  color: var(--color-text-primary);
}

.drawer-close {
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 14px;
  cursor: pointer;
}

.drawer-close:hover {
  background: var(--color-surface-raised);
  color: var(--color-text-primary);
}

.drawer-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.identity {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.identity-avatar {
  width: 48px;
  height: 48px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--color-accent);
  color: var(--color-on-accent);
  font-size: 20px;
  font-weight: 650;
}

.identity-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.identity-name {
  font-size: var(--font-section);
  font-weight: 650;
  color: var(--color-text-primary);
}

.identity-sub {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.block {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border);
}

.block-title {
  font-size: var(--font-sm);
  font-weight: 650;
  color: var(--color-text-secondary);
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.block-value {
  font-size: var(--font-body);
  color: var(--color-text-primary);
}

.facts {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0;
}

.pwd-actions {
  display: flex;
  justify-content: flex-end;
}

.fact {
  display: flex;
  gap: var(--space-3);
  align-items: baseline;
  padding: 6px 0;
  border-bottom: 1px dashed var(--color-border);
}

.fact:last-child {
  border-bottom: 0;
}

.fact dt {
  flex: 0 0 84px;
  font-size: var(--font-sm);
  color: var(--color-text-muted);
}

.fact dd {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: var(--font-sm);
  color: var(--color-text-primary);
  overflow-wrap: anywhere;
}

.mono {
  font-family: 'Fira Code', ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: var(--font-xs);
}

.drawer-foot {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-5);
  border-top: 1px solid var(--color-border);
}

.btn-ghost {
  height: 34px;
  padding: 0 14px;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  font-size: var(--font-sm);
  cursor: pointer;
}

.btn-ghost:hover {
  background: var(--color-surface-raised);
}

@media (max-width: 767px) {
  .drawer {
    width: 100vw;
  }
}
</style>
