<script setup>
import { reactive, ref } from 'vue'
import { NAlert, NButton, NForm, NFormItem, NInput, NModal, NSpace } from 'naive-ui'
import { api } from '../../../api/client'

// 保存成功交还页面处理（关闭弹窗 + notice/错误横幅 + 重新加载）；
// 请求失败留在弹窗内展示，用户可直接改完重试。
const props = defineProps({
  account: { type: Object, required: true },
})
const emit = defineEmits(['close', 'saved'])

const form = reactive({ appId: '', appSecret: '' })
const busy = ref(false)
const error = ref('')

async function submit() {
  busy.value = true
  error.value = ''
  try {
    const res = await api.put(`/wechat-mp/accounts/${props.account.id}/credentials`, {
      appId: form.appId.trim(),
      appSecret: form.appSecret.trim(),
    })
    emit('saved', res)
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <n-modal
    :show="true"
    preset="card"
    :title="`修改凭据 · ${account.displayName}`"
    style="width: min(480px, calc(100vw - 32px))"
    :mask-closable="!busy"
    @update:show="(value) => { if (!value) emit('close') }"
  >
    <p class="page-sub" style="margin-top: 0">保存前会真实调用微信接口验证；Secret 加密存储且永不回传。</p>
    <n-alert v-if="error" type="error" :bordered="false" style="margin-bottom: 12px">{{ error }}</n-alert>
    <n-form label-placement="top">
      <n-form-item label="AppID">
        <n-input v-model:value="form.appId" placeholder="wx 开头的应用 ID" autocomplete="off" />
      </n-form-item>
      <n-form-item label="AppSecret">
        <n-input
          v-model:value="form.appSecret"
          type="password"
          show-password-on="click"
          placeholder="公众号后台生成，仅保存加密副本"
          autocomplete="new-password"
        />
      </n-form-item>
    </n-form>
    <template #footer>
      <n-space justify="end">
        <n-button quaternary :disabled="busy" @click="emit('close')">取消</n-button>
        <n-button
          type="primary"
          :loading="busy"
          :disabled="!form.appId.trim() || !form.appSecret.trim()"
          @click="submit"
        >
          保存并验证
        </n-button>
      </n-space>
    </template>
  </n-modal>
</template>
