<script setup>
import { computed, ref } from 'vue'
import { NAlert, NButton, NInput } from 'naive-ui'
import { useSessionStore } from '../stores/session'

const session = useSessionStore()

// login | register
const mode = ref('login')
const email = ref('')
const password = ref('')
const confirm = ref('')
const displayName = ref('')
const busy = ref(false)
const error = ref('')

const minLength = computed(() => session.authConfig?.passwordMinLength || 8)
const canRegister = computed(() => Boolean(session.allowRegistration))
const isRegister = computed(() => mode.value === 'register')

const hint = computed(() =>
  isRegister.value
    ? session.bootstrapAvailable
      ? '首次部署：创建第一个账号后即可使用'
      : '注册后自动登录'
    : '使用邮箱与密码登录',
)

function switchMode() {
  mode.value = isRegister.value ? 'login' : 'register'
  error.value = ''
  confirm.value = ''
}

function validate() {
  if (!email.value.trim()) return '请输入邮箱'
  if (!isRegister.value && !password.value) return '请输入密码'
  if (isRegister.value) {
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.value.trim())) return '邮箱格式不正确'
    if (password.value.length < minLength.value) return `密码至少 ${minLength.value} 位`
    if (password.value !== confirm.value) return '两次输入的密码不一致'
  }
  return ''
}

async function submit() {
  if (busy.value) return
  const problem = validate()
  if (problem) {
    error.value = problem
    return
  }
  busy.value = true
  error.value = ''
  try {
    if (isRegister.value) {
      await session.register({
        email: email.value.trim(),
        password: password.value,
        displayName: displayName.value.trim(),
      })
    } else {
      await session.loginWithPassword(email.value.trim(), password.value)
    }
    // 成功后 session.needsLogin 变 false，App.vue 自动切到主界面
    password.value = ''
    confirm.value = ''
  } catch (err) {
    error.value = err?.message || '登录失败，请稍后再试'
  } finally {
    busy.value = false
  }
}

async function devEnter() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const subject = localStorage.getItem('hanyue-subject') || 'local-creator'
    await session.loginWithSubject(subject, '创作者')
  } catch (err) {
    error.value = err?.message || '开发态进入失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <div class="card login-card">
      <header class="login-head">
        <div class="brand">汉月</div>
        <h1 class="login-title">{{ isRegister ? '创建账号' : '登录工作台' }}</h1>
        <p class="page-sub">{{ hint }}</p>
      </header>

      <form class="login-form" @submit.prevent="submit">
        <label class="field">
          <span class="field-label">邮箱</span>
          <n-input
            v-model:value="email"
            placeholder="you@example.com"
            :input-props="{ autocomplete: 'username', name: 'email' }"
            :disabled="busy"
          />
        </label>

        <label v-if="isRegister" class="field">
          <span class="field-label">显示名</span>
          <n-input
            v-model:value="displayName"
            placeholder="留空则用邮箱前缀"
            :input-props="{ autocomplete: 'nickname' }"
            :disabled="busy"
          />
        </label>

        <label class="field">
          <span class="field-label">密码</span>
          <n-input
            v-model:value="password"
            type="password"
            show-password-on="click"
            :placeholder="isRegister ? `至少 ${minLength} 位` : '请输入密码'"
            :input-props="{
              autocomplete: isRegister ? 'new-password' : 'current-password',
              name: 'password',
            }"
            :disabled="busy"
          />
        </label>

        <label v-if="isRegister" class="field">
          <span class="field-label">确认密码</span>
          <n-input
            v-model:value="confirm"
            type="password"
            show-password-on="click"
            placeholder="再输一次"
            :input-props="{ autocomplete: 'new-password' }"
            :disabled="busy"
          />
        </label>

        <n-alert v-if="error" type="error" :bordered="false" class="login-error">
          {{ error }}
        </n-alert>

        <n-button type="primary" attr-type="submit" :loading="busy" block size="large">
          {{ isRegister ? '注册并进入' : '登录' }}
        </n-button>
      </form>

      <div class="login-foot">
        <button v-if="canRegister" type="button" class="link" @click="switchMode">
          {{ isRegister ? '已有账号？去登录' : '没有账号？立即注册' }}
        </button>
        <button
          v-if="session.devLoginAvailable"
          type="button"
          class="link link-muted"
          :disabled="busy"
          @click="devEnter"
        >
          开发态免密进入
        </button>
      </div>

      <p v-if="session.devLoginAvailable" class="login-tip">
        开发态入口不校验密码，仅建议在本人机器或内网使用；公网部署请关闭
        <code>ALLOW_DEV_LOGIN</code>。
      </p>
    </div>
  </div>
</template>

<style scoped>
.login-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-8) var(--space-4);
  min-height: 60vh;
}

.login-card {
  width: 100%;
  max-width: 400px;
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.login-head {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.brand {
  font-family: 'Source Han Serif SC', 'Noto Serif SC', 'Songti SC', Georgia, serif;
  font-size: var(--font-sm);
  letter-spacing: 0.32em;
  color: var(--color-text-secondary);
  text-transform: uppercase;
}

.login-title {
  font-family: 'Source Han Serif SC', 'Noto Serif SC', 'Songti SC', Georgia, serif;
  font-size: var(--font-display);
  font-weight: 650;
  line-height: 1.2;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.field-label {
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}

.login-error {
  margin: 0;
}

.login-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.link {
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  font-size: var(--font-sm);
  color: var(--color-accent, #3b82f6);
}

.link:hover {
  text-decoration: underline;
}

.link-muted {
  color: var(--color-text-secondary);
}

.link:disabled {
  opacity: 0.5;
  cursor: default;
}

.login-tip {
  font-size: var(--font-xs);
  line-height: 1.6;
  color: var(--color-text-tertiary, var(--color-text-secondary));
  margin: 0;
}

.login-tip code {
  font-family: 'Fira Code', monospace;
  font-size: 0.92em;
}
</style>
