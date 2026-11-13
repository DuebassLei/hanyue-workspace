import { computed, onBeforeUnmount, ref, watch } from 'vue'

const ACTIVE = new Set(['pending_review', 'approved', 'queued', 'publishing'])

export function useJobsPolling(refresh, interval = 12000, maxInterval = 60000) {
  const lastUpdatedAt = ref(null)
  const polling = ref(false)
  const activeCount = ref(0)
  let timer = null
  let currentInterval = interval

  const hasActiveJobs = computed(() => activeCount.value > 0)

  function stop() {
    if (timer) clearTimeout(timer)
    timer = null
    polling.value = false
  }

  function schedule() {
    stop()
    if (!hasActiveJobs.value) return
    polling.value = true
    timer = setTimeout(async () => {
      const ok = await refresh()
      lastUpdatedAt.value = new Date()
      currentInterval = ok ? interval : Math.min(currentInterval * 2, maxInterval)
      schedule()
    }, currentInterval)
  }

  function updateJobs(jobs) {
    activeCount.value = jobs.filter((job) => ACTIVE.has(job.status)).length
  }

  function updateSummary(summary) {
    activeCount.value = ['pending_review', 'approved', 'queued', 'publishing']
      .reduce((count, status) => count + (summary?.byStatus?.[status] || 0), 0)
  }

  watch(hasActiveJobs, (active) => {
    if (!active) {
      currentInterval = interval
      stop()
    } else schedule()
  })
  onBeforeUnmount(stop)

  return { hasActiveJobs, lastUpdatedAt, polling, updateJobs, updateSummary, stop }
}
