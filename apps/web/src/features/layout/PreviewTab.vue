<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { NAlert, NInput, NSelect, NTag } from 'naive-ui'
import PhonePreview from './PhonePreview.vue'

/**
 * 样文预览：编辑任意正文 → 选主题 → 375px 手机壳实时渲染，
 * 附字数统计与兼容性检查结果（lint issues / 渲染 warnings）。
 * 真实推送仍在创作空间的发布抽屉完成，这里只负责「看得见」。
 */
const props = defineProps({
  store: { type: Object, required: true },
})

const SAMPLE = `# 为什么公众号需要「排版系统」

:::lead
读者判断一篇文章的质量，只需要滑动屏幕的前三秒。排版不是装饰，是内容的第一印象。
:::

正文段落支持 **加粗**、*斜体*、==高亮==、\`行内代码\` 与 [链接]（公众号会降级为文本）。

## 三种主题，三种气质

同一篇正文，换主题就是换文章：

:::compare 裸文本 / 主题排版
样式随手改、篇篇不同 | 样式收进 tokens、一键换装
:::

:::stat
10 套 | 内置预设主题
18 项 | 可调排版参数
:::

## 发布前检查

:::checklist
done | 主题在手机上预览过
done | 外链已降级或移除
todo | 封面图 1 张
:::

:::quote-card 一位排版编辑
好的排版是透明的：读者只看见内容，看不见样式。
:::

> 本页面的预览 HTML 与推送到公众号草稿箱的是**同一份**——渲染只发生在服务端一处。

:::follow
觉得有用？点个关注，下一篇讲「字号与行高的舒适区」。
:::

:::sign
狸知 · 微信排版样文
:::`

const body = ref(SAMPLE)
const themeId = ref('')

const themeOptions = computed(() =>
  props.store.visiblePresets.value
    .concat(props.store.themes.value.custom)
    .map((t) => ({ value: t.id, label: t.name })),
)

const result = computed(() => props.store.previewResult.value)
const stats = computed(() => result.value?.stats || null)
const notices = computed(() => [
  ...(result.value?.warnings || []),
  ...(result.value?.issues || []),
])

watch([body, themeId], () => {
  props.store.schedulePreview(body.value, null, themeId.value)
})

onMounted(() => {
  themeId.value = props.store.themes.value.defaultThemeId || themeOptions.value[0]?.value || ''
  props.store.renderPreview(body.value, null, themeId.value)
})
</script>

<template>
  <div class="preview-tab">
    <div class="preview-editor">
      <div class="preview-editor-head">
        <n-select
          v-model:value="themeId"
          :options="themeOptions"
          size="small"
          class="preview-theme-select"
          placeholder="选择主题"
        />
        <div class="spacer" />
        <span v-if="stats" class="field-hint">
          正文 {{ stats.sourceChars }} 字 · 渲染后 {{ stats.renderedChars }} 字 · 约 {{ stats.readingMinutes }} 分钟
        </span>
      </div>
      <n-input
        v-model:value="body"
        type="textarea"
        :autosize="{ minRows: 10, maxRows: 24 }"
        placeholder="粘贴或输入正文，支持标题 / 引用 / 列表 / ::: 小组件语法"
      />
      <n-alert
        v-if="notices.length"
        class="preview-notices"
        :type="notices.some((i) => i.level === 'error') ? 'error' : 'warning'"
        :bordered="false"
        :title="`兼容性与告警（${notices.length}）`"
      >
        <ul class="preview-notice-list">
          <li v-for="(notice, index) in notices" :key="index">
            <n-tag size="tiny" :type="notice.level === 'error' ? 'error' : 'warning'" :bordered="false">
              {{ notice.level === 'error' ? '错误' : '提醒' }}
            </n-tag>
            {{ notice.message }}
          </li>
        </ul>
      </n-alert>
    </div>
    <PhonePreview
      :html="result?.html || ''"
      :busy="store.previewBusy.value"
      :error="store.previewError.value"
      :height="600"
    />
  </div>
</template>

<style scoped>
.preview-tab {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 24px;
  align-items: start;
}
@media (max-width: 980px) {
  .preview-tab {
    grid-template-columns: 1fr;
  }
}
.preview-editor {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}
.preview-editor-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.preview-theme-select {
  width: 200px;
}
.preview-notices {
  font-size: 12px;
}
.preview-notice-list {
  margin: 0;
  padding-left: 4px;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
</style>
