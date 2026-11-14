import { computed, onBeforeUnmount, ref } from 'vue'
import { api } from '../api/client'

export function useStoryboard() {
  const projects = ref([])
  const project = ref(null)
  const selectedSceneId = ref('')
  const selectedShotId = ref('')
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')

  const scenes = computed(() => project.value?.scenes || [])
  const selectedScene = computed(() => scenes.value.find((scene) => scene.id === selectedSceneId.value) || scenes.value[0] || null)
  const shots = computed(() => selectedScene.value?.shots || [])
  const selectedShot = computed(() => shots.value.find((shot) => shot.id === selectedShotId.value) || shots.value[0] || null)

  function selectScene(sceneId) {
    selectedSceneId.value = sceneId || ''
    const scene = scenes.value.find((item) => item.id === selectedSceneId.value)
    selectedShotId.value = scene?.shots?.[0]?.id || ''
  }

  function selectShot(shotId) {
    selectedShotId.value = shotId || ''
  }

  async function loadProjects() {
    projects.value = await api.get('/storyboards')
  }

  async function deleteProject(projectId) {
    await api.del(`/storyboards/${projectId}`)
    projects.value = projects.value.filter((item) => item.id !== projectId)
    if (project.value?.id === projectId) project.value = null
  }

  function trashPath(type, id, action) {
    const path = type === 'project' ? `/storyboards/${id}` : `/storyboards/${type}/${id}`
    return action === 'permanent' ? `${path}/permanent` : `${path}/restore`
  }

  async function deletePermanently(type, id) {
    await api.del(trashPath(type, id, 'permanent'))
  }

  async function restore(type, id) {
    await api.post(trashPath(type, id, 'restore'))
  }

  async function loadProject(projectId) {
    loading.value = true
    error.value = ''
    try {
      project.value = await api.get(`/storyboards/${projectId}`)
      selectedSceneId.value = selectedSceneId.value || project.value.scenes?.[0]?.id || ''
      const scene = project.value.scenes?.find((item) => item.id === selectedSceneId.value) || project.value.scenes?.[0]
      selectedSceneId.value = scene?.id || ''
      selectedShotId.value = selectedShotId.value || scene?.shots?.[0]?.id || ''
    } catch (err) {
      error.value = err.message
      throw err
    } finally {
      loading.value = false
    }
  }

  async function createProject(payload = {}) {
    const fresh = await api.post('/storyboards', { title: '未命名分镜', ...payload })
    projects.value = [fresh, ...projects.value]
    await loadProject(fresh.id)
    return fresh
  }

  async function patchProject(payload) {
    saving.value = true
    try {
      project.value = await api.patch(`/storyboards/${project.value.id}`, payload)
    } finally {
      saving.value = false
    }
  }

  async function createScene(payload = {}) {
    const scene = await api.post(`/storyboards/${project.value.id}/scenes`, { title: '新场景', ...payload })
    project.value.scenes = [...(project.value.scenes || []), { ...scene, shots: [] }]
    selectScene(scene.id)
    return scene
  }

  async function patchScene(sceneId, payload) {
    const scene = await api.patch(`/storyboards/scenes/${sceneId}`, payload)
    const index = project.value.scenes.findIndex((item) => item.id === sceneId)
    if (index >= 0) project.value.scenes[index] = { ...project.value.scenes[index], ...scene }
    return scene
  }

  async function deleteScene(sceneId) {
    await api.del(`/storyboards/scenes/${sceneId}`)
    project.value.scenes = project.value.scenes.filter((item) => item.id !== sceneId)
    selectScene(project.value.scenes[0]?.id || '')
  }

  async function createShot(sceneId, payload = {}) {
    const shot = await api.post(`/storyboards/scenes/${sceneId}/shots`, payload)
    const scene = project.value.scenes.find((item) => item.id === sceneId)
    scene.shots = [...(scene.shots || []), shot]
    selectScene(sceneId)
    selectShot(shot.id)
    return shot
  }

  async function patchShot(shotId, payload) {
    const shot = await api.patch(`/storyboards/shots/${shotId}`, payload)
    for (const scene of project.value.scenes) {
      const index = scene.shots.findIndex((item) => item.id === shotId)
      if (index >= 0) scene.shots[index] = { ...scene.shots[index], ...shot }
    }
    return shot
  }

  async function deleteShot(shotId) {
    await api.del(`/storyboards/shots/${shotId}`)
    for (const scene of project.value.scenes) scene.shots = scene.shots.filter((item) => item.id !== shotId)
    selectedShotId.value = shots.value[0]?.id || ''
  }

  async function reorderShots(sceneId, ids) {
    await api.put(`/storyboards/scenes/${sceneId}/shots/order`, { ids })
    const scene = project.value.scenes.find((item) => item.id === sceneId)
    scene.shots.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id))
  }

  function replaceShot(shot) {
    for (const scene of project.value?.scenes || []) {
      const index = scene.shots.findIndex((item) => item.id === shot.id)
      if (index >= 0) scene.shots[index] = shot
    }
  }

  async function refresh() {
    if (project.value?.id) await loadProject(project.value.id)
  }

  onBeforeUnmount(() => {
    project.value = null
  })

  async function breakDownProject(sourceText) {
    const result = await api.post(`/storyboards/${project.value.id}/breakdown`, { sourceText })
    project.value = result.project
    selectScene(project.value.scenes?.at(-1)?.id || '')
    return result
  }

  async function breakDownScene(sceneId, sourceText) {
    const result = await api.post(`/storyboards/scenes/${sceneId}/breakdown`, { sourceText })
    project.value = result.project
    selectScene(sceneId)
    return result
  }

  async function saveAssets(assets) {
    return patchProject({ assets })
  }

  return {
    projects, project, scenes, selectedScene, shots, selectedShot, selectedSceneId, selectedShotId,
    loading, saving, error, loadProjects, loadProject, createProject, patchProject, deleteProject,
    deletePermanently, restore, createScene, patchScene, deleteScene, createShot, patchShot, deleteShot, reorderShots, replaceShot,
    selectScene, selectShot, refresh, breakDownProject, breakDownScene, saveAssets,
  }
}
