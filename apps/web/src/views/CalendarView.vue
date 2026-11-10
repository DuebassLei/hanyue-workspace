<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'
import AppModal from '../components/AppModal.vue'

const entries = ref([])
const loading = ref(true)
const error = ref('')
const showForm = ref(false)
const saving = ref(false)
const form = ref({ title: '', plannedAt: '', platform: '' })

const PLATFORMS = [
  { code: '', label: '不指定' },
  { code: 'wechat_official', label: '公众号' },
]

const grouped = computed(() => {
  const map = new Map()
  for (const entry of entries.value) {
    const day = entry.plannedAt.slice(0, 10)
    if (!map.has(day)) map.set(day, [])
    map.get(day).push(entry)
  }
  return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]))
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    entries.value = await api.get('/calendar')
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

onMounted(load)

function openForm() {
  const now = new Date()
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset())
  form.value = { title: '', plannedAt: now.toISOString().slice(0, 16), platform: '' }
  showForm.value = true
}

async function submit() {
  if (!form.value.plannedAt) return
  saving.value = true
  try {
    const created = await api.post('/calendar', {
      title: form.value.title || '未命名排期',
      plannedAt: new Date(form.value.plannedAt).toISOString(),
      platform: form.value.platform || null,
    })
    entries.value.push(created)
    entries.value.sort((a, b) => a.plannedAt.localeCompare(b.plannedAt))
    showForm.value = false
  } catch (err) {
    error.value = err.message
  } finally {
    saving.value = false
  }
}

function platformLabel(code) {
  return PLATFORMS.find((p) => p.code === code)?.label || ''
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">内容日历</h1>
        <p class="page-sub">排期决定节奏，不决定质量</p>
      </div>
      <button class="btn" @click="openForm">安排一期</button>
    </div>

    <div v-if="error" class="banner banner-danger" role="alert">{{ error }}</div>

    <div v-if="loading" class="stack">
      <div v-for="i in 3" :key="i" class="skeleton" style="height: 80px" />
    </div>

    <div v-else-if="!entries.length" class="card">
      <div class="empty">
        <span class="empty-title">日历是空的</span>
        <span>给一篇稿件安排具体时间，发布才不会拖到最后一天。</span>
        <button class="btn btn-sm" @click="openForm">安排第一期</button>
      </div>
    </div>

    <div v-else class="stack">
      <section v-for="[day, items] in grouped" :key="day" class="card">
        <h2 class="card-title">{{ day }}</h2>
        <div class="list">
          <div v-for="entry in items" :key="entry.id" class="list-row">
            <div class="list-main">
              <div class="list-title">{{ entry.title }}</div>
              <div class="list-meta">
                {{ entry.plannedAt.slice(11, 16) }}
                <span v-if="entry.platform"> · {{ platformLabel(entry.platform) }}</span>
              </div>
            </div>
            <span class="tag">{{ entry.status === 'planned' ? '待发布' : entry.status }}</span>
          </div>
        </div>
      </section>
    </div>

    <AppModal v-if="showForm" title="安排一期内容" @close="showForm = false">
      <div class="stack">
        <label class="field">
          <span class="field-label">标题</span>
          <input v-model="form.title" class="input" placeholder="这期准备发什么" />
        </label>
        <label class="field">
          <span class="field-label">计划时间</span>
          <input v-model="form.plannedAt" type="datetime-local" class="input" />
        </label>
        <label class="field">
          <span class="field-label">平台</span>
          <select v-model="form.platform" class="select">
            <option v-for="p in PLATFORMS" :key="p.code" :value="p.code">{{ p.label }}</option>
          </select>
        </label>
        <div class="row">
          <div class="spacer" />
          <button class="btn btn-ghost" @click="showForm = false">取消</button>
          <button class="btn" :disabled="saving" @click="submit">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </AppModal>
  </div>
</template>
