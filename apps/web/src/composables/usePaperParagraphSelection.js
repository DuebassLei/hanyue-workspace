import { computed, ref } from 'vue'

export function usePaperParagraphSelection(getBlocks) {
  const active = ref(false)
  const selections = ref([])
  const ids = computed(() => getBlocks().filter((block) => selections.value.includes(block.id)).map((block) => block.id))
  let anchorId = ''
  const touched = ref(false)

  function start(fallbackId) {
    active.value = true
    if (!touched.value && !ids.value.length && getBlocks().some((block) => block.id === fallbackId)) {
      selections.value = [fallbackId]
      anchorId = fallbackId
    }
    touched.value = true
  }

  function stop() {
    active.value = false
  }

  function toggle(id, extend = false) {
    const blocks = getBlocks()
    const to = blocks.findIndex((block) => block.id === id)
    if (to < 0) return
    touched.value = true
    const from = extend && anchorId !== id ? blocks.findIndex((block) => block.id === anchorId) : -1
    if (from >= 0) {
      const range = blocks.slice(Math.min(from, to), Math.max(from, to) + 1).map((block) => block.id)
      selections.value = [...new Set([...ids.value, ...range])]
      return
    }
    selections.value = ids.value.includes(id) ? ids.value.filter((item) => item !== id) : [...ids.value, id]
    anchorId = id
  }

  function clear() {
    selections.value = []
    anchorId = ''
    touched.value = true
  }

  function reset() {
    stop()
    clear()
    touched.value = false
  }

  return { active, ids, touched, start, stop, toggle, clear, reset }
}
