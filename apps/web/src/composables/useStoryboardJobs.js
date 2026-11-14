import { onBeforeUnmount, watch } from 'vue'
import { api } from '../api/client'

export function useStoryboardJobs(project, onUpdated) {
  let timer = null
  let inFlight = false

  async function poll() {
    if (inFlight || !project.value?.id) return
    const active = (project.value.scenes || []).flatMap((scene) => scene.shots || []).filter((shot) =>
      ['queued', 'generating'].includes(shot.sketchStatus) || ['queued', 'generating'].includes(shot.finalStatus),
    )
    if (!active.length) return
    inFlight = true
    try {
      for (const shot of active) {
        for (const stage of ['sketch', 'final']) {
          const status = shot[`${stage}Status`]
          const jobId = shot[`${stage}JobId`]
          if (!jobId || !['queued', 'generating'].includes(status)) continue
          const job = await api.get(`/jobs/${jobId}`)
          if (job.status === 'succeeded' || job.status === 'failed' || (job.status === 'running' && status === 'queued')) {
            await onUpdated()
          }
        }
      }
    } catch {
      // A transient status read failure should not stop subsequent recovery attempts.
    } finally {
      inFlight = false
    }
  }

  function start() {
    if (timer) return
    timer = setInterval(poll, 1800)
  }

  function stop() {
    if (timer) clearInterval(timer)
    timer = null
  }

  watch(project, (value) => {
    if (value?.id) start()
    else stop()
  }, { immediate: true })
  onBeforeUnmount(stop)
  return { poll, stop }
}
