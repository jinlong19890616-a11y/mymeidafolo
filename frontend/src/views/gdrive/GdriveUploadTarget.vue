<script setup lang="ts">
import { gdriveUploadApi, type GdriveUploadSettings, type GdriveUploadTarget } from '@/api/gdrive'

const props = defineProps<{ settings?: GdriveUploadSettings }>()
const target = defineModel<GdriveUploadTarget>({ required: true })
const fetched = ref<GdriveUploadSettings>()
const accounts = computed(() => {
  const settings = props.settings || fetched.value
  return (settings?.accounts || [])
    .filter(account => account.enabled)
    .map(account => ({
      title: `${account.name}${settings?.credential_status?.[account.id] ? '' : ' · 未配置 Token'}`,
      value: account.id,
    }))
})
const error = ref('')
const controller = new AbortController()
onMounted(async () => {
  if (props.settings) return
  try {
    const result = await gdriveUploadApi.settings(controller.signal)
    if (controller.signal.aborted) return
    if (!result.success || !result.data) throw new Error()
    fetched.value = result.data
  } catch {
    if (!controller.signal.aborted) error.value = '上传账号加载失败，请关闭后重试。'
  }
})
onUnmounted(() => controller.abort())
</script>

<template>
  <div class="d-flex flex-column ga-3">
    <VAlert v-if="error" type="error" variant="tonal">{{ error }}</VAlert>
    <VSelect
      v-model="target.account_ids"
      :items="accounts"
      label="参与上传的账号"
      multiple
      chips
      :mobile-layout="false"
    />
    <VTextField v-model="target.drive_id" label="团队共享盘 ID" :mobile-layout="false" />
    <VTextField
      v-model="target.folder_id"
      label="上传暂存文件夹 ID（drmedia）"
      hint="先上传到此目录；共享盘根目录可填写共享盘 ID"
      persistent-hint
      :mobile-layout="false"
    />
    <VTextField
      v-model="target.path"
      label="上传暂存相对路径"
      hint="目录任务按本地整理结构上传到此前缀；单文件填写含文件名的路径"
      persistent-hint
      :mobile-layout="false"
    />
    <VTextField
      v-model="target.archive_folder_id"
      label="云端归档文件夹 ID（media）"
      hint="与暂存目录位于同一共享盘。填写后先上传，再移动到二次分类目录；留空兼容原直接上传"
      persistent-hint
      :mobile-layout="false"
    />
    <VTextField
      v-if="target.archive_folder_id"
      v-model="target.archive_path"
      label="云端归档相对路径"
      hint="目录任务填写分类目录前缀；单文件填写最终含文件名的路径，留空沿用上传路径"
      persistent-hint
      :mobile-layout="false"
    />
    <VAlert v-if="target.archive_folder_id" type="info" variant="tonal">
      本地整理完成目录 → 上传 drmedia → 云端二次分类归档至 media → 实时 STRM → 本地资料 → Emby 扫库。
    </VAlert>
    <VTextField
      v-model="target.strm_prefix"
      label="STRM 播放路径或地址前缀"
      hint="填写最终 media 归档根目录对应的播放路径或地址；归档核验后按最终路径生成 STRM"
      persistent-hint
      :mobile-layout="false"
    />
    <VSwitch v-model="target.metadata_enabled" label="生成 STRM 后准备本地元数据" hide-details />
    <VAlert v-if="target.metadata_enabled" type="info" variant="tonal">
      复用源目录的 NFO、海报和背景图，输出到 STRM 旁边；不在线识别。缺少 NFO 或已有文件冲突时保留源文件并提示失败。
    </VAlert>
    <VTextField
      v-model="target.refresh_server"
      label="完成后扫库的媒体服务器名称"
      hint="填写系统媒体服务器中已启用的 Emby 名称；留空不触发扫库"
      persistent-hint
      :mobile-layout="false"
    />
    <VTextField
      v-model="target.refresh_library_id"
      label="Emby 媒体库 ID"
      hint="与服务器名称同时填写；元数据准备完成后才请求扫描指定媒体库"
      persistent-hint
      :mobile-layout="false"
    />
  </div>
</template>
