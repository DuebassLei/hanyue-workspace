<script setup>
import { reactive, ref } from 'vue'
import { NAlert, NButton, NCheckbox, NForm, NFormItem, NInput, NModal, NSpace } from 'naive-ui'
import { api } from '../../../api/client'

// 绑定成功/失败都交还页面处理（notice / 全局错误横幅 / 重新加载账号列表），
// 本组件只负责表单收集与一次绑定请求。
const emit = defineEmits(['close', 'bound', 'error'])

// 组件由 v-if 挂载，每次打开都是全新状态，无需在关闭时重置表单
const form = reactive({ displayName: '', useOwnCred: false, appId: '', appSecret: '' })
const busy = ref(false)

const credsIncomplete = () => form.useOwnCred && (!form.appId.trim() || !form.appSecret.trim())

async function submit() {
  busy.value = true
  try {
    const payload = { displayName: form.displayName }
    // 多账号：勾选独立凭据时，AppID/AppSecret 加密存到该账号上；留空走部署级默认凭据
    if (form.useOwnCred && form.appId.trim() && form.appSecret.trim()) {
      payload.credentials = {
        appId: form.appId.trim(),
        appSecret: form.appSecret.trim(),
      }
    }
    const res = await api.post('/wechat-mp/bind', payload)
    emit(
      'bound',
      `验证通过：AppID ${res.probe.appId}，后台现有草稿 ${res.probe.draftTotal} 篇。正式群发仍需在公众号后台操作。`,
    )
  } catch (err) {
    emit('error', err.message)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <n-modal
    :show="true"
    preset="card"
    title="绑定并验证公众号"
    style="width: min(520px, calc(100vw - 32px))"
    :mask-closable="!busy"
    @update:show="(value) => { if (!value) emit('close') }"
  >
    <p class="page-sub" style="margin-top: 0">同一平台可绑定多个账号；每个账号可持有自己的凭据。</p>
    <n-form label-placement="top">
      <n-form-item label="账号备注">
        <n-input v-model:value="form.displayName" placeholder="例如：主号 / 分公司号" />
      </n-form-item>
      <n-form-item :show-label="false">
        <n-checkbox v-model:checked="form.useOwnCred">
          为该账号使用独立 AppID/AppSecret（不勾选则使用默认凭据）
        </n-checkbox>
      </n-form-item>
      <template v-if="form.useOwnCred">
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
      </template>
    </n-form>
    <n-alert type="info" :bordered="false">
      AppID / AppSecret 在公众号后台「设置与开发 → 基本配置」获取；绑定会真实调用微信接口验证，
      请先在公众号后台「IP 白名单」中加入本机出口 IP。同一 AppID 重复绑定会更新原账号，不同 AppID 会新建账号。
    </n-alert>
    <template #footer>
      <n-space justify="end">
        <n-button quaternary :disabled="busy" @click="emit('close')">取消</n-button>
        <n-button type="primary" :loading="busy" :disabled="credsIncomplete()" @click="submit">
          绑定并验证
        </n-button>
      </n-space>
    </template>
  </n-modal>
</template>
