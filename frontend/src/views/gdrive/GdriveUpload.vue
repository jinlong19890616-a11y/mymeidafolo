<script setup lang="ts">
import {
  gdriveApi,
  gdriveBytes,
  gdriveUploadApi,
  type GdriveTask,
  type GdriveUploadSettings,
  type GdriveUploadTarget as UploadTarget,
} from '@/api/gdrive'
import { useUserStore } from '@/stores'
import GdriveUploadTarget from './GdriveUploadTarget.vue'

const user = useUserStore()
const config = ref<GdriveUploadSettings>({ enabled: false, concurrency: 4, chunk_size_mb: 16, accounts: [] })
const credentials = ref<Record<string, boolean>>({})
const configuredAccounts = ref<string[]>([])
const savedSettings = ref<GdriveUploadSettings>()
const loaded = ref(false)
const busy = ref(false)
const error = ref('')
const message = ref('')
const uncertain = ref(false)
const tasks = ref<GdriveTask[]>([])
const source = ref('')
const strmRoot = ref('')
const removeSource = ref(false)
const target = ref<UploadTarget>({ account_ids: [], drive_id: '', folder_id: '', path: '', strm_prefix: '' })
const credentialId = ref('')
const secret = reactive({ token: '', client_id: '', client_secret: '' })
const checks = ref<{ id: string; success: boolean; message: string }[]>([])
const controller = new AbortController()
let timer: ReturnType<typeof setTimeout> | undefined
const validTarget = computed(
  () => target.value.account_ids.length && target.value.drive_id.trim() && target.value.folder_id.trim(),
)

async function load() {
  clearTimeout(timer)
  try {
    const [settings, list] = await Promise.all([
      gdriveUploadApi.settings(controller.signal),
      gdriveApi.list(controller.signal),
    ])
    if (controller.signal.aborted) return
    if (!settings.success || !settings.data) throw new Error()
    configuredAccounts.value = settings.data.accounts.map(account => account.id)
    savedSettings.value = settings.data
    credentials.value = settings.data.credential_status || {}
    if (!loaded.value) {
      config.value = settings.data
      loaded.value = true
    }
    tasks.value = list.filter(task => task.backend === 'api')
    if (tasks.value.some(task => !['completed', 'failed', 'cancelled'].includes(task.status)))
      timer = setTimeout(() => void load(), 5000)
  } catch {
    if (!controller.signal.aborted) error.value = '上传配置或任务加载失败，请刷新重试。'
  }
}

async function action(
  operation: () => Promise<{ success: boolean; message?: string }>,
  success: string,
  creation = false,
) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    const result = await operation()
    if (controller.signal.aborted) return
    if (!result.success) error.value = result.message || '操作失败，请检查配置。'
    else {
      message.value = success
      await load()
    }
  } catch {
    if (!controller.signal.aborted) {
      if (creation) uncertain.value = true
      error.value = creation
        ? '请求结果未确认，请刷新任务列表核对后再创建，避免重复提交。'
        : '操作结果未确认，请刷新核对后重试。'
    }
  } finally {
    busy.value = false
  }
}

function addAccount() {
  config.value.accounts.push({
    id: crypto.randomUUID(),
    name: `账号 ${config.value.accounts.length + 1}`,
    enabled: true,
    concurrency: 1,
  })
}
function saveConfig() {
  void action(() => gdriveUploadApi.save({ ...config.value, credential_status: undefined }), '上传配置已保存。')
}
async function saveCredential() {
  const id = credentialId.value
  await action(async () => {
    const result = await gdriveUploadApi.credential(id, { ...secret })
    if (result.success) {
      credentials.value[id] = true
      credentialId.value = ''
      secret.token = secret.client_id = secret.client_secret = ''
    }
    return result
  }, '账号 Token 已保存，页面不会回显。')
}
function closeCredential() {
  credentialId.value = ''
  secret.token = secret.client_id = secret.client_secret = ''
}
watch(
  () => user.superUser,
  value => {
    if (!value) closeCredential()
  },
)
async function checkTarget() {
  await action(async () => {
    const result = await gdriveUploadApi.check(target.value)
    checks.value = result.data || []
    return result
  }, '所选账号均可写入目标共享盘。')
}
function create() {
  void action(
    () =>
      gdriveUploadApi.create({
        source_path: source.value.trim(),
        target: target.value,
        strm_root: strmRoot.value.trim(),
        remove_source: removeSource.value,
      }),
    '直接上传任务已入队。',
    true,
  )
}
onMounted(load)
onUnmounted(() => {
  clearTimeout(timer)
  controller.abort()
  closeCredential()
})
</script>

<template>
  <div class="d-flex flex-column ga-4">
    <VAlert type="info" variant="tonal"
      >通过 Google Drive API
      直接上传到团队共享盘。选择参与上传的账号，总并发与每账号并发共同生效；每个文件固定由一个账号断点续传。</VAlert
    >
    <VAlert v-if="error" type="error" variant="tonal">{{ error }}</VAlert>
    <VAlert v-if="message" type="success" variant="tonal">{{ message }}</VAlert>
    <VBtn variant="tonal" :disabled="busy" @click="load">刷新上传状态</VBtn>
    <VCard v-if="loaded && user.superUser" title="上传账号与并发">
      <VCardText class="d-flex flex-column ga-3">
        <VSwitch v-model="config.enabled" label="启用直接上传" color="primary" />
        <VSwitch
          v-model="config.notifications_enabled"
          label="发送上传、归档、STRM 与失败记录到 Telegram"
          color="primary"
          :disabled="busy"
        />
        <VSwitch
          v-model="config.daily_report_enabled"
          label="每 24 小时发送上传总量与阶段记录汇总（含零上传）"
          color="primary"
          :disabled="busy"
        />
        <VTextField
          v-model="config.notification_source"
          label="Telegram 通知渠道名称"
          hint="填写通知中心已启用的 Telegram 渠道名称；留空使用首个可用渠道。共用 Bot 时配置仅发送模式。"
          persistent-hint
          :disabled="busy"
        />
        <div class="upload-settings-grid">
          <VTextField
            v-model.number="config.concurrency"
            type="number"
            min="1"
            max="16"
            label="总上传并发"
            :mobile-layout="false"
          />
          <VTextField
            v-model.number="config.chunk_size_mb"
            type="number"
            min="1"
            max="64"
            label="上传分块大小（MiB）"
            :mobile-layout="false"
          />
        </div>
        <div class="upload-settings-grid">
          <div v-for="account in config.accounts" :key="account.id" class="upload-account pa-4">
            <VTextField v-model="account.name" label="账号名称" :mobile-layout="false" />
            <VTextField
              v-model.number="account.concurrency"
              type="number"
              min="1"
              max="16"
              label="该账号并发"
              :mobile-layout="false"
            />
            <VSwitch v-model="account.enabled" label="账号可参与上传" color="primary" />
            <p class="text-caption">{{ credentials[account.id] ? 'Token 已配置' : 'Token 未配置' }}</p>
            <VBtn
              variant="tonal"
              :disabled="busy || !configuredAccounts.includes(account.id)"
              @click="credentialId = account.id"
              >设置 Token</VBtn
            >
          </div>
        </div>
        <p class="text-caption text-medium-emphasis">
          添加账号后先保存配置，再设置 Token。OAuth 自动续期需要 refresh_token、client_id 和
          client_secret；账号须具有目标共享盘写入权限。
        </p>
        <div class="d-flex flex-wrap ga-3">
          <VBtn variant="tonal" :disabled="busy" @click="addAccount">添加上传账号</VBtn>
          <VBtn :loading="busy" @click="saveConfig">保存上传配置</VBtn>
        </div>
      </VCardText>
    </VCard>
    <VCard v-if="loaded" title="新建直接上传">
      <VCardText class="d-flex flex-column ga-3">
        <VTextField v-model="source" label="服务器源文件路径" :mobile-layout="false" />
        <GdriveUploadTarget v-model="target" :settings="savedSettings" />
        <VTextField v-model="strmRoot" label="STRM 输出根目录（可选）" :mobile-layout="false" />
        <VSwitch v-model="removeSource" label="云端校验及 STRM 成功后删除源文件" color="primary" />
        <VAlert v-for="item in checks" :key="item.id" :type="item.success ? 'success' : 'error'" variant="tonal"
          >{{ config.accounts.find(account => account.id === item.id)?.name || item.id }} · {{ item.message }}</VAlert
        >
        <div class="d-flex flex-wrap ga-3">
          <VBtn v-if="user.superUser" variant="tonal" :disabled="!validTarget || busy" @click="checkTarget"
            >检查账号与共享盘权限</VBtn
          >
          <VBtn
            :disabled="!validTarget || !source.startsWith('/') || !savedSettings?.enabled || busy || uncertain"
            :loading="busy"
            @click="create"
            >开始上传</VBtn
          >
        </div>
      </VCardText>
    </VCard>
    <VCard title="直接上传队列">
      <VCardText v-if="!tasks.length">暂无直接上传任务。</VCardText>
      <VCardText v-for="task in tasks" :key="task.id" class="upload-task">
        <p class="text-break">#{{ task.id }} · {{ task.src_path }}</p>
        <p>
          {{ task.status }} ·
          {{ config.accounts.find(account => account.id === task.upload_account_id)?.name || '等待分配账号' }} ·
          {{ gdriveBytes(task.uploaded_size) }} / {{ gdriveBytes(task.source_size) }}
        </p>
        <VProgressLinear :model-value="task.progress" class="my-2" />
        <p class="text-break">
          {{ task.archive_path }} <span v-if="task.remote_file_id">· 云端 ID：{{ task.remote_file_id }}</span>
        </p>
        <p v-if="task.error_message" class="text-error">{{ task.error_message }}</p>
        <div class="d-flex flex-wrap ga-2 mt-2">
          <VBtn
            v-if="['failed', 'cancelled'].includes(task.status)"
            variant="tonal"
            :disabled="busy"
            @click="action(() => gdriveUploadApi.retry(task.id), '上传任务已重新入队。')"
            >重试上传</VBtn
          >
          <VBtn
            v-else-if="task.status !== 'completed'"
            variant="tonal"
            :disabled="busy"
            @click="action(() => gdriveUploadApi.cancel(task.id), '已请求取消上传。')"
            >取消上传</VBtn
          >
        </div>
      </VCardText>
    </VCard>
    <VDialog
      v-if="credentialId"
      :model-value="!!credentialId"
      max-width="640"
      scrollable
      :persistent="busy"
      @update:model-value="!busy && closeCredential()"
    >
      <VCard title="设置账号 Token">
        <VCardText>
          <div class="d-flex flex-column ga-4 pt-3">
            <VTextarea v-model="secret.token" label="Token JSON（只写）" autocomplete="off" :mobile-layout="false" />
            <VTextField v-model="secret.client_id" label="OAuth client_id" autocomplete="off" :mobile-layout="false" />
            <VTextField
              v-model="secret.client_secret"
              label="OAuth client_secret"
              type="password"
              autocomplete="new-password"
              :mobile-layout="false"
            />
          </div>
        </VCardText>
        <VCardActions
          ><VSpacer /><VBtn :disabled="busy" @click="closeCredential">关闭</VBtn
          ><VBtn :disabled="!secret.token || busy" @click="saveCredential">保存 Token</VBtn></VCardActions
        >
      </VCard>
    </VDialog>
  </div>
</template>

<style scoped>
.upload-settings-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1rem;
}
.upload-account {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  min-inline-size: 0;
  background: rgba(var(--v-theme-on-surface), 0.04);
  border-radius: 8px;
}
@media (max-width: 959px) {
  .upload-settings-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
.upload-task {
  overflow-wrap: anywhere;
  border-block-start: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
</style>
