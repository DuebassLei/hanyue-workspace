<script setup>
import { computed, onMounted } from 'vue'
import { NButton, NSelect, NTag } from 'naive-ui'
import { useModelSelectionStore } from '../stores/modelSelection'
import { toast } from '../ui/feedback'

const props = defineProps({
  label: { type: String, default: '模型' },
  showDefaultActions: { type: Boolean, default: true },
  disabled: { type: Boolean, default: false },
})

const store = useModelSelectionStore()
onMounted(() => store.load())

const isDefaultSelected = computed(
  () => !!store.selectedModelId && store.selectedModelId === store.defaultModelId,
)
const canSetDefault = computed(
  () => !!store.selectedModelId && !isDefaultSelected.value,
)

async function setDefault() {
  try {
    await store.setDefault(store.selectedModelId)
    toast('已设为默认模型，未选择时优先使用')
  } catch (err) {
    toast(`设为默认失败：${err.message}`)
  }
}

async function clearDefault() {
  try {
    await store.setDefault('')
    toast('已清除默认模型，恢复按候选链自动选择')
  } catch (err) {
    toast(`清除默认失败：${err.message}`)
  }
}
</script>

<template>
  <div class="model-selector">
    <div class="model-selector-row">
      <label class="model-selector-label">{{ props.label }}</label>
      <n-select
        v-model:value="store.selectedModelId"
        class="model-selector-select"
        size="small"
        :options="store.options"
        :loading="store.loading"
        :disabled="props.disabled || store.loading"
        :consistent-menu-width="false"
        aria-label="选择本次生成使用的模型"
      />
      <n-button
        v-if="props.showDefaultActions && canSetDefault"
        size="tiny"
        quaternary
        @click="setDefault"
      >设为默认</n-button>
      <n-tag v-else-if="props.showDefaultActions && isDefaultSelected" size="small" type="success" round>默认</n-tag>
      <n-button
        v-if="props.showDefaultActions && store.defaultModelId"
        size="tiny"
        quaternary
        @click="clearDefault"
      >清除默认</n-button>
    </div>
    <p v-if="store.defaultModelIgnored" class="model-selector-hint model-selector-warn">
      默认模型当前不可用（{{ store.defaultModelIgnored.message }}），已按候选链生成；设置保留，恢复后自动生效。
    </p>
    <p v-else-if="store.selectedModelId" class="model-selector-hint">
      已手动指定模型：调用失败会直接报错并附供应商原因，不会自动换模型。
    </p>
    <p v-else class="model-selector-hint">
      未选择时优先用默认模型，失败自动切换到下一个文本模型。
    </p>
    <p v-if="store.error" class="model-selector-hint model-selector-warn">{{ store.error }}</p>
  </div>
</template>

<style scoped>
.model-selector { min-width: 0; }
.model-selector-row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.model-selector-label { color: #827a71; font-size: 12px; white-space: nowrap; }
.model-selector-select { flex: 1 1 220px; min-width: 180px; max-width: 420px; }
.model-selector-hint { margin: 4px 0 0; color: #948b81; font-size: 11px; line-height: 1.5; }
.model-selector-warn { color: #9b3e37; }
</style>
