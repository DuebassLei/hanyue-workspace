import { computed, ref } from 'vue'
import { api } from '../api/client'

/** 视频创作（图文成片）会话状态：项目列表 + 当前项目 + 渲染任务轮询。 */
export function useVideo() {
  const projects = ref([])
  const project = ref(null)
  const error = ref('')
  const provider = ref(null)
  /** 渲染任务实时状态：{ status, stage, progress, jobId, errorMessage } */
  const renderTask = ref(null)

  let timer = null
  let inFlight = false

  const rendering = computed(() => {
    const s = renderTask.value?.status || project.value?.status
    return s === 'rendering'
  })

  async function loadProvider() {
    try {
      provider.value = await api.get('/video-provider/status')
    } catch {
      provider.value = null
    }
  }

  async function loadProjects() {
    projects.value = await api.get('/videos')
  }

  async function loadProject(id) {
    project.value = await api.get(`/videos/${id}`)
    renderTask.value = null
    if (project.value.status === 'rendering') await pollRender()
    startPolling()
    return project.value
  }

  async function createProject(payload) {
    const created = await api.post('/videos', payload)
    await loadProjects()
    project.value = created
    renderTask.value = null
    return created
  }

  async function patchProject(patch) {
    project.value = await api.patch(`/videos/${project.value.id}`, patch)
    return project.value
  }

  async function deleteProject(id) {
    await api.del(`/videos/${id}`)
    project.value = null
    renderTask.value = null
    stopPolling()
    await loadProjects()
  }

  async function breakdown(modelId) {
    const body = modelId ? { modelId } : {}
    project.value = await api.post(`/videos/${project.value.id}/breakdown`, body)
    renderTask.value = null
    return project.value
  }

  async function addShot(payload) {
    const shot = await api.post(`/videos/${project.value.id}/shots`, payload)
    await reload()
    return shot
  }

  async function patchShot(shotId, patch) {
    await api.patch(`/videos/shots/${shotId}`, patch)
    await reload()
  }

  async function deleteShot(shotId) {
    await api.del(`/videos/shots/${shotId}`)
    await reload()
  }

  async function reorderShots(shotIds) {
    project.value = await api.post(`/videos/${project.value.id}/shots/reorder`, shotIds)
  }

  async function render(reset = false) {
    const res = await api.post(`/videos/${project.value.id}/render`, { reset })
    if (project.value) project.value.status = 'rendering'
    renderTask.value = { status: 'rendering', stage: null, progress: 0, jobId: res.jobId }
    startPolling()
    return res
  }

  async function cancelRender() {
    const jobId = renderTask.value?.jobId || project.value?.renderJobId
    if (jobId) await api.post(`/jobs/${jobId}/cancel`)
    await pollRender()
  }

  async function pollRender() {
    if (inFlight || !project.value?.id) return
    inFlight = true
    try {
      const res = await api.get(`/videos/${project.value.id}/render-status`)
      renderTask.value = {
        status: res.status,
        stage: res.job?.stage || null,
        progress: res.job?.progress ?? null,
        jobId: res.job?.id || project.value.renderJobId,
        errorMessage: res.renderError || res.job?.errorMessage || null,
      }
      // 任务态与项目态收敛后刷新镜头（配音时长/配图在任务中落库）
      if (res.status !== 'rendering') {
        stopPolling()
        await reload()
      } else if (res.job && ['succeeded', 'failed', 'cancelled'].includes(res.job.status)) {
        await reload()
      }
    } catch {
      // 瞬时读取失败不打断轮询，下一轮重试
    } finally {
      inFlight = false
    }
  }

  async function reload() {
    if (!project.value?.id) return
    const id = project.value.id
    project.value = await api.get(`/videos/${id}`)
    if (project.value.status !== 'rendering') stopPolling()
  }

  function startPolling() {
    if (timer) return
    timer = setInterval(pollRender, 1800)
  }

  function stopPolling() {
    if (timer) clearInterval(timer)
    timer = null
  }

  function reset() {
    project.value = null
    renderTask.value = null
    stopPolling()
  }

  return {
    projects, project, error, provider, renderTask, rendering,
    loadProvider, loadProjects, loadProject, createProject, patchProject, deleteProject,
    breakdown, addShot, patchShot, deleteShot, reorderShots,
    render, cancelRender, reload, reset, stopPolling,
  }
}
