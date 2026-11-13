<script setup>
import { computed, onMounted, ref } from 'vue'
import { NAlert, NButton, NDatePicker, NEmpty, NForm, NFormItem, NInput, NModal, NSelect, NSpace, NTag } from 'naive-ui'
import { api } from '../api/client'

const entries = ref([])
const loading = ref(true)
const error = ref('')
const showForm = ref(false)
const saving = ref(false)
const form = ref({ title: '', plannedAt: null, platform: null })

const PLATFORMS = [
  { code: '', label: '不指定' },
  { code: 'wechat_official', label: '公众号' },
]

const platformOptions = PLATFORMS.map((item) => ({
  value: item.code || null,
  label: item.label,
}))

const plannedAtTs = computed({
  get: () => form.value.plannedAt,
  set: (value) => {
    form.value.plannedAt = value
  },
})

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
  form.value = { title: '', plannedAt: Date.now(), platform: null }
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
      <n-button type="primary" @click="openForm">安排一期</n-button>
    </div>

    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>

    <div v-if="loading" class="stack">
      <div v-for="i in 3" :key="i" class="skeleton" style="height: 80px" />
    </div>

    <div v-else-if="!entries.length" class="card">
      <n-empty description="给一篇稿件安排具体时间，发布才不会拖到最后一天。">
        <template #extra>
          <n-button type="primary" size="small" @click="openForm">安排第一期</n-button>
        </template>
      </n-empty>
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
            <n-tag size="small" round>{{ entry.status === 'planned' ? '待发布' : entry.status }}</n-tag>
          </div>
        </div>
      </section>
    </div>

    <n-modal v-model:show="showForm" preset="card" title="安排一期内容" style="width: min(460px, calc(100vw - 32px))" :mask-closable="!saving">
      <n-form label-placement="top">
        <n-form-item label="标题">
          <n-input v-model:value="form.title" placeholder="这期准备发什么" />
        </n-form-item>
        <n-form-item label="计划时间">
          <n-date-picker v-model:value="plannedAtTs" type="datetime" clearable style="width: 100%" />
        </n-form-item>
        <n-form-item label="平台">
          <n-select v-model:value="form.platform" :options="platformOptions" placeholder="不指定" clearable />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button quaternary :disabled="saving" @click="showForm = false">取消</n-button>
          <n-button type="primary" :loading="saving" :disabled="!form.plannedAt" @click="submit">保存</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>
