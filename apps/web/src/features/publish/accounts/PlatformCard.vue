<script setup>
import { NButton, NEmpty, NSpace } from 'naive-ui'
import StatusTag from '../../../components/StatusTag.vue'
import EgressIpWidget from './EgressIpWidget.vue'

// 当前平台的账号列表与维护操作；请求和页面级状态由父页面协调。
defineProps({
  platform: { type: Object, required: true },
  // 公众号 IP 白名单展示用，多账号行共享页面级探测结果
  egress: { type: Object, default: null },
  egressLoading: { type: Boolean, default: false },
  egressCopied: { type: Boolean, default: false },
  // 只有 publishing.view 时整卡转只读：绑定/改凭据/验证/解绑都会落到 publishing.use
  readonly: { type: Boolean, default: false },
  readonlyReason: { type: String, default: '' },
})
defineEmits(['bind', 'verify', 'unbind', 'edit-credential', 'egress-refresh', 'egress-copy'])
</script>

<template>
  <section class="platform-card">
    <template v-if="platform.available">
      <n-empty v-if="!platform.accounts.length" description="绑定并验证后即可使用该平台的发布能力。">
        <template #extra>
          <n-button
            type="primary"
            size="small"
            :disabled="readonly"
            :title="readonlyReason || '绑定账号'"
            @click="$emit('bind', platform.platform)"
          >
            绑定账号
          </n-button>
        </template>
      </n-empty>
      <div v-else class="list">
        <div v-for="account in platform.accounts" :key="account.id" class="account-block">
          <div class="account-head">
            <div class="list-title">{{ account.displayName }}</div>
            <StatusTag :status="account.status" />
          </div>
          <div class="list-meta">
            {{ account.hasOwnCredential ? `独立凭据 ${account.appIdMasked}` : '使用默认凭据（服务端配置）' }}
            · 来源：{{ account.authSource === 'app_secret' ? '官方凭据' : account.authSource === 'demo' ? '历史演示账号（无凭据）' : '待验证' }}
          </div>
          <EgressIpWidget
            v-if="account.platform === 'wechat_official' && !readonly"
            :ip="egress?.ip || ''"
            :loading="egressLoading"
            :copied="egressCopied"
            @refresh="$emit('egress-refresh')"
            @copy="$emit('egress-copy')"
          />
          <n-space class="account-actions" :size="6">
            <n-button
              v-if="account.platform === 'wechat_official' && account.authSource !== 'demo'"
              size="small"
              quaternary
              :disabled="readonly"
              :title="readonlyReason || '修改该账号的 AppID/AppSecret'"
              @click="$emit('edit-credential', account)"
            >
              修改凭据
            </n-button>
            <n-button
              v-if="account.authSource !== 'demo'"
              size="small"
              quaternary
              :disabled="readonly"
              :title="readonlyReason || '真实调用平台接口验证凭据'"
              @click="$emit('verify', account)"
            >
              验证授权
            </n-button>
            <n-button
              v-if="account.status !== 'unbound'"
              size="small"
              type="error"
              quaternary
              :disabled="readonly"
              :title="readonlyReason || '解绑后该账号不再参与发布'"
              @click="$emit('unbind', account)"
            >
              解绑
            </n-button>
          </n-space>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.platform-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

/* 账号信息与操作分两行，避免窄列里把中文挤成一字一行 */
.account-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  min-width: 0;
}

.account-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  min-width: 0;
}

.account-head .list-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.account-block .list-meta {
  overflow-wrap: anywhere;
}

.account-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

/* 卡头副标题与页面队列区共用外观，scoped 下需各自声明 */
.section-subtitle {
  margin: 4px 0 0;
  color: var(--color-text-muted);
  font-size: var(--font-xs);
}
</style>
