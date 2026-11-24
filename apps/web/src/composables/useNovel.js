import { computed, onBeforeUnmount, ref } from 'vue'
import { api, getToken } from '../api/client'
import { useModelSelectionStore } from '../stores/modelSelection'

const POLL_MS = 2000

export function useNovel() {
  const modelSelection = useModelSelectionStore()
  // 会话级选择作用于本次调用；不选择时省略 modelId，服务端按默认 + 降级解析。
  // 队列任务的模型会在入队那一刻快照，重启重投也按同一模型执行。
  const modelBody = () => (modelSelection.requestModelId ? { modelId: modelSelection.requestModelId } : {})

  const projects = ref([])
  const project = ref(null)
  const selectedChapterId = ref('')
  const loading = ref(false)
  const error = ref('')
  const job = ref(null)
  const warnings = ref([])

  let timer = null
  let generation = 0
  let loadGeneration = 0
  let saveQueue = Promise.resolve()
  let saveError = null
  const saving = ref(0)
  const starting = ref(false)

  function queueSave(action) {
    if (!saving.value) saveError = null
    saving.value++
    const pending = saveQueue.then(action)
    saveQueue = pending.catch((err) => { saveError = err })
    return pending.finally(() => { saving.value-- })
  }

  const chapters = computed(() => project.value?.chapters || [])
  const characters = computed(() => project.value?.characters || [])
  const aliases = computed(() => project.value?.aliases || [])
  const selectedChapter = computed(
    () => chapters.value.find((chapter) => chapter.id === selectedChapterId.value) || chapters.value[0] || null,
  )
  const jobBusy = computed(() => job.value && (job.value.status === 'queued' || job.value.status === 'running'))

  function selectChapter(chapterId) {
    selectedChapterId.value = chapterId || ''
  }

  function stopPolling() {
    generation++
    loadGeneration++
    if (timer) clearTimeout(timer)
    timer = null
  }

  function applyChapter(next) {
    if (!project.value || !next?.id) return
    const index = project.value.chapters.findIndex((item) => item.id === next.id)
    if (index >= 0) project.value.chapters[index] = { ...project.value.chapters[index], ...next }
  }

  async function loadProjects() {
    projects.value = await api.get('/novels')
  }

  async function loadProject(projectId) {
    if (project.value?.id !== projectId) {
      stopPolling()
      job.value = null
      warnings.value = []
      selectedChapterId.value = ''
    }
    const request = ++loadGeneration
    loading.value = true
    error.value = ''
    try {
      const loaded = await api.get(`/novels/${projectId}`)
      if (request !== loadGeneration) return
      project.value = loaded
      const stillThere = project.value.chapters?.some((item) => item.id === selectedChapterId.value)
      if (!stillThere) selectedChapterId.value = project.value.chapters?.[0]?.id || ''
    } catch (err) {
      if (request === loadGeneration) error.value = err.message
      throw err
    } finally {
      if (request === loadGeneration) loading.value = false
    }
  }

  async function refresh() {
    if (project.value?.id) await loadProject(project.value.id)
  }

  async function createProject(payload) {
    const fresh = await api.post('/novels', {
      title: payload.title,
      sourceText: payload.sourceText,
      rightsConfirmed: true,
    })
    projects.value = [{ id: fresh.id, title: fresh.title, status: fresh.status, chapterCount: 0, updatedAt: fresh.updatedAt }, ...projects.value]
    stopPolling()
    job.value = null
    project.value = fresh
    selectedChapterId.value = ''
    warnings.value = []
    return fresh
  }

  function patchProject(payload) {
    const projectId = project.value.id
    return queueSave(async () => {
      const next = await api.patch(`/novels/${projectId}`, payload)
      if (project.value?.id === projectId) project.value = { ...project.value, ...next, chapters: next.chapters || project.value.chapters }
      const row = projects.value.find((item) => item.id === next.id)
      if (row && next.title) row.title = next.title
      return next
    })
  }

  async function deleteProject(projectId) {
    await api.del(`/novels/${projectId}`)
    projects.value = projects.value.filter((item) => item.id !== projectId)
    if (project.value?.id === projectId) {
      stopPolling()
      job.value = null
      project.value = null
      selectedChapterId.value = ''
      warnings.value = []
    }
  }

  async function split() {
    const list = await api.post(`/novels/${project.value.id}/split`)
    await refresh()
    selectedChapterId.value = project.value?.chapters?.[0]?.id || list?.[0]?.id || ''
    return list
  }

  async function patchChapter(chapterId, payload) {
    const next = await api.patch(`/novels/${project.value.id}/chapters/${chapterId}`, payload)
    applyChapter(next)
    return next
  }

  function watchJob(jobId) {
    stopPolling()
    const token = generation
    const projectId = project.value?.id
    job.value = { id: jobId, status: 'queued', kind: '', errorCode: '', errorMessage: '', result: null }

    const tick = async () => {
      if (token !== generation || project.value?.id !== projectId) return
      try {
        const current = await api.get(`/novels/${projectId}/jobs/${jobId}`)
        if (token !== generation || project.value?.id !== projectId) return
        job.value = current
        if (current.status === 'succeeded' || current.status === 'failed') {
          timer = null
          if (current.status === 'failed') error.value = current.errorMessage || '任务失败'
          if (Array.isArray(current.result?.warnings)) warnings.value = current.result.warnings
          await refresh()
          return
        }
      } catch (err) {
        if (token !== generation || project.value?.id !== projectId) return
        error.value = err.message
      }
      if (token === generation && project.value?.id === projectId) timer = setTimeout(tick, POLL_MS)
    }

    timer = setTimeout(tick, POLL_MS)
  }

  async function extract() {
    const started = await api.post(`/novels/${project.value.id}/extract`, modelBody())
    watchJob(started.jobId)
    return started
  }

  async function rewrite(chapterId) {
    const started = await api.post(`/novels/${project.value.id}/chapters/${chapterId}/rewrite`, modelBody())
    watchJob(started.jobId)
    return started
  }

  async function accept(chapterId) {
    const next = await api.post(`/novels/${project.value.id}/chapters/${chapterId}/accept`, modelBody())
    applyChapter(next)
    await refresh()
    return next
  }

  async function revert(chapterId) {
    const result = await api.post(`/novels/${project.value.id}/chapters/${chapterId}/revert`)
    if (result?.chapter) applyChapter(result.chapter)
    await refresh()
    return result
  }

  async function continueNext() {
    if (starting.value) return
    starting.value = true
    const projectId = project.value.id
    try {
      await saveQueue
      if (saveError) throw saveError
      if (project.value?.id !== projectId) return
      warnings.value = []
      const started = await api.post(`/novels/${projectId}/continue`, modelBody())
      if (project.value?.id !== projectId) return started
      watchJob(started.jobId)
      await refresh()
      if (project.value?.id === projectId && started.chapterId) selectedChapterId.value = started.chapterId
      return started
    } finally {
      starting.value = false
    }
  }

  function patchCharacter(characterId, payload) {
    const projectId = project.value.id
    return queueSave(async () => {
      const next = await api.patch(`/novels/${projectId}/characters/${characterId}`, payload)
      if (project.value?.id === projectId) {
        const index = project.value.characters.findIndex((item) => item.id === characterId)
        if (index >= 0) project.value.characters[index] = { ...project.value.characters[index], ...next }
      }
      return next
    })
  }

  function patchAlias(aliasId, payload) {
    const projectId = project.value.id
    return queueSave(async () => {
      const next = await api.patch(`/novels/${projectId}/aliases/${aliasId}`, payload)
      if (project.value?.id === projectId) {
        const index = project.value.aliases.findIndex((item) => item.id === aliasId)
        if (index >= 0) project.value.aliases[index] = { ...project.value.aliases[index], ...next }
      }
      return next
    })
  }

  async function exportFile(format) {
    const response = await fetch(`/api/v1/novels/${project.value.id}/export?format=${format}`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    })
    if (!response.ok) {
      let message = '导出失败'
      try {
        const payload = await response.json()
        message = payload?.detail?.message || payload?.message || message
      } catch {
        /* 非 JSON 错误体保持默认文案 */
      }
      const err = new Error(message)
      err.code = 'export_failed'
      throw err
    }
    const blob = await response.blob()
    const disposition = response.headers.get('Content-Disposition') || ''
    const matched = disposition.match(/filename\*=UTF-8''([^;]+)|filename="([^"]+)"/)
    const filename = decodeURIComponent(matched?.[1] || matched?.[2] || `novel.${format}`)
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = filename
    link.click()
    URL.revokeObjectURL(link.href)
  }

  onBeforeUnmount(stopPolling)

  return {
    projects,
    project,
    chapters,
    characters,
    aliases,
    selectedChapterId,
    selectedChapter,
    loading,
    error,
    job,
    jobBusy,
    saving,
    starting,
    warnings,
    selectChapter,
    stopPolling,
    loadProjects,
    loadProject,
    refresh,
    createProject,
    patchProject,
    deleteProject,
    split,
    patchChapter,
    extract,
    rewrite,
    accept,
    revert,
    continueNext,
    patchCharacter,
    patchAlias,
    exportFile,
  }
}
