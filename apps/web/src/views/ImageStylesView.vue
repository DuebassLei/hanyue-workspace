<script setup>
/**
 * 风格库已并入统一提示词库（/prompts），本页仅作兼容重定向壳。
 * B 侧 router 也会把 /image-styles 重定向到 /prompts，这里是双保险。
 */
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NAlert, NButton, NSpace } from 'naive-ui'

const router = useRouter()

function go() {
  router.replace('/prompts?type=cover')
}

onMounted(go)
</script>

<template>
  <div class="redirect-shell">
    <NAlert type="info" title="风格库已合并到提示词库">
      封面 / 基础 / 配图风格现在统一在「提示词库」中管理，正在为你跳转…
      <template #action>
        <NSpace>
          <NButton size="small" type="primary" @click="go">立即前往</NButton>
        </NSpace>
      </template>
    </NAlert>
  </div>
</template>

<style scoped>
.redirect-shell {
  max-width: 720px;
  margin: var(--space-6) auto;
  padding: 0 var(--space-3);
}
</style>
