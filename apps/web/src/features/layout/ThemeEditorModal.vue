<script setup>
import { computed, reactive, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NColorPicker,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NSelect,
  NSlider,
  NSpace,
  NTabs,
  NTabPane,
} from 'naive-ui'
import PhonePreview from './PhonePreview.vue'
import { toast } from '../../ui/feedback'

/**
 * 主题编辑器：字段按后端 token_schema 渲染（校验范围不重复维护），
 * 左侧表单 + 右侧手机壳实时预览（raw tokens 走服务端同一渲染函数）。
 */
const props = defineProps({
  open: { type: Boolean, default: false },
  store: { type: Object, required: true },
  /** { theme?, baseThemeId? }：编辑已有主题传 theme；新建传 baseThemeId（可为空=默认） */
  initial: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['close', 'saved'])

const SAMPLE_BODY = `# 排版的分寸

正文示例：**重点加粗**、==高亮强调==、` + '`行内代码`' + `。公众号正文只认内联样式，所以主题的每一项都要在这里看到真实效果。

## 小标题样式

> 引用块用来放补充说明，底色与左线各来自一个 token。

- 列表符号由 listMarker 控制
- 段间距看 paragraphGap

:::stat
1.8x | 行高建议值
:::

:::card 编辑器说明
改左边的任意控件，右侧会实时重新渲染；只有这里显示的效果，推送后才会一样。
:::`

const name = ref('')
const tokens = reactive({})
const saving = ref(false)
const saveError = ref('')
const body = ref(SAMPLE_BODY)

const editingId = computed(() => props.initial.theme?.id || '')
const groups = computed(() => {
  const map = new Map()
  for (const field of props.store.schema.value) {
    if (!map.has(field.group)) map.set(field.group, [])
    map.get(field.group).push(field)
  }
  return [...map.entries()]
})

function resetFrom(sourceTokens, sourceName, fallbackBaseName) {
  name.value = sourceName || fallbackBaseName || '未命名主题'
  saveError.value = ''
  for (const key of Object.keys(tokens)) delete tokens[key]
  const base = props.store.schema.value.reduce((acc, f) => {
    acc[f.key] = f.default
    return acc
  }, {})
  Object.assign(base, sourceTokens || {})
  Object.assign(tokens, base)
}

watch(
  () => props.open,
  (open) => {
    if (!open) return
    const theme = props.initial.theme
    if (theme) {
      resetFrom(theme.tokens, theme.name)
      return
    }
    const baseId = props.initial.baseThemeId
    const base = props.store.themes.value.presets.find((t) => t.id === baseId)
    resetFrom(base?.tokens, '', base ? `${base.name} 副本` : '我的主题')
  },
)

// 编辑时实时预览未保存的草稿：raw tokens 直接送服务端渲染
watch(
  () => [JSON.stringify(tokens), body.value],
  () => {
    if (!props.open) return
    props.store.schedulePreview(body.value, { ...tokens })
  },
)

watch(
  () => props.open,
  (open) => {
    if (open) props.store.renderPreview(body.value, { ...tokens })
  },
)

async function save() {
  saving.value = true
  saveError.value = ''
  try {
    const saved = await props.store.saveTheme({
      name: name.value.trim(),
      tokens: { ...tokens },
      editingId: editingId.value,
    })
    toast(editingId.value ? `已保存「${saved.name}」` : `已创建主题「${saved.name}」`)
    emit('saved', saved)
    emit('close')
  } catch (err) {
    // 后端 normalize 的中文校验消息（范围/颜色/未知键）原样展示
    saveError.value = err.message
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <n-modal
    :show="open"
    preset="card"
    :title="editingId ? '编辑主题' : '新建主题'"
    :style="{ width: '960px', maxWidth: '96vw' }"
    :bordered="false"
    :mask-closable="false"
    @update:show="(v) => !v && emit('close')"
  >
    <div class="editor-layout">
      <div class="editor-form">
        <n-form label-placement="top" size="small">
          <n-form-item label="主题名称">
            <n-input v-model:value="name" :maxlength="60" show-count placeholder="给主题起个名字" />
          </n-form-item>
        </n-form>

        <n-tabs type="line" size="small" animated>
          <n-tab-pane v-for="[group, fields] in groups" :key="group" :name="group" :tab="group">
            <div class="editor-fields">
              <div v-for="field in fields" :key="field.key" class="editor-field">
                <div class="editor-field-head">
                  <span class="editor-field-label">{{ field.label }}</span>
                  <span class="editor-field-key">{{ field.key }}</span>
                </div>
                <template v-if="field.kind === 'color'">
                  <n-color-picker
                    v-model:value="tokens[field.key]"
                    :modes="['hex']"
                    size="small"
                    :show-alpha="false"
                  />
                </template>
                <template v-else-if="field.kind === 'number'">
                  <div class="editor-slider-row">
                    <n-slider
                      v-model:value="tokens[field.key]"
                      :min="field.min"
                      :max="field.max"
                      :step="field.step"
                      :tooltip="true"
                    />
                    <span class="editor-slider-value">{{ tokens[field.key] }}</span>
                  </div>
                </template>
                <template v-else-if="field.kind === 'enum'">
                  <n-select v-model:value="tokens[field.key]" :options="field.options" size="small" />
                </template>
                <template v-else>
                  <n-input v-model:value="tokens[field.key]" size="small" type="textarea" :autosize="{ minRows: 1, maxRows: 3 }" />
                </template>
              </div>
            </div>
          </n-tab-pane>
        </n-tabs>

        <n-alert v-if="saveError" type="error" :bordered="false" class="editor-error">{{ saveError }}</n-alert>
      </div>

      <div class="editor-preview">
        <div class="editor-preview-label">实时预览（服务端同一渲染管线）</div>
        <n-input
          v-model:value="body"
          type="textarea"
          :autosize="{ minRows: 3, maxRows: 7 }"
          class="editor-sample"
          placeholder="样文正文，支持 ::: 区块语法"
        />
        <PhonePreview
          :html="store.previewResult.value?.html || ''"
          :busy="store.previewBusy.value"
          :error="store.previewError.value"
          :height="430"
          :scale="0.92"
        />
      </div>
    </div>

    <template #footer>
      <n-space justify="end">
        <n-button size="small" @click="emit('close')">取消</n-button>
        <n-button size="small" type="primary" :loading="saving" @click="save">保存主题</n-button>
      </n-space>
    </template>
  </n-modal>
</template>

<style scoped>
.editor-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 380px;
  gap: 20px;
  align-items: start;
}
@media (max-width: 900px) {
  .editor-layout {
    grid-template-columns: 1fr;
  }
}
.editor-form {
  min-width: 0;
  max-height: 62vh;
  overflow-y: auto;
  padding-right: 4px;
}
.editor-fields {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 4px;
}
.editor-field-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 4px;
}
.editor-field-label {
  font-size: 13px;
}
.editor-field-key {
  font-size: 11px;
  color: var(--color-text-secondary, #aaa);
  font-family: 'Fira Code', monospace;
}
.editor-slider-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.editor-slider-value {
  min-width: 40px;
  text-align: right;
  font-size: 12px;
  color: var(--color-text-secondary, #888);
}
.editor-error {
  margin-top: 12px;
}
.editor-preview {
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: center;
}
.editor-preview-label {
  font-size: 12px;
  color: var(--color-text-secondary, #888);
  align-self: flex-start;
}
.editor-sample {
  width: 100%;
}
</style>
