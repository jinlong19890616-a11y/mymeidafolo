<script setup lang="ts">
import {
  gdriveApi,
  gdriveBytes,
  gdriveTaskTitle,
  GDRIVE_STATUSES,
  type GdriveHistory,
  type GdriveTask,
} from '@/api/gdrive'
import { useI18n } from 'vue-i18n'

const props = defineProps<{ task: GdriveTask }>()
const emit = defineEmits<{ close: [] }>()
const { t } = useI18n()
const history = ref<GdriveHistory[]>([])
const loading = ref(false)
const failed = ref(false)
let controller: AbortController | undefined
const statusText = (value: string) =>
  GDRIVE_STATUSES.some(status => status === value) ? t(`gdrive.status.${value}`) : value

async function loadHistory() {
  controller?.abort()
  const request = new AbortController()
  controller = request
  loading.value = true
  failed.value = false
  try {
    const records = await gdriveApi.history(props.task.id, request.signal)
    if (!request.signal.aborted) history.value = records
  } catch {
    if (!request.signal.aborted) failed.value = true
  } finally {
    if (!request.signal.aborted) loading.value = false
  }
}
watch(
  () => props.task.id,
  () => {
    history.value = []
    void loadHistory()
  },
  { immediate: true },
)
onUnmounted(() => controller?.abort())
</script>

<template>
  <VDialog :model-value="true" :aria-label="t('gdrive.details')" max-width="760" @update:model-value="emit('close')">
    <VCard :title="t('gdrive.details')" :subtitle="`#${task.id} · ${gdriveTaskTitle(task)}`">
      <VCardText>
        <div class="d-flex flex-wrap ga-3 mb-4">
          <VChip>{{ statusText(task.status) }}</VChip>
          <span>{{ t('gdrive.stage') }}: {{ statusText(task.stage) }}</span>
          <span>{{ t('gdrive.retries') }}: {{ task.retry_count }}</span>
        </div>
        <VProgressLinear :model-value="task.progress" height="8" rounded :aria-label="t('gdrive.progress')" />
        <p class="text-caption my-2">
          {{ task.progress }}% · {{ gdriveBytes(task.uploaded_size) }} / {{ gdriveBytes(task.source_size) }}
        </p>
        <p class="text-caption text-medium-emphasis mb-4">{{ t('gdrive.progressHint') }}</p>
        <VAlert v-if="task.error_message" type="error" variant="tonal" class="mb-4">{{ task.error_message }}</VAlert>
        <dl class="gdrive-paths">
          <template
            v-for="item in [
              ['sourcePath', task.src_path],
              ['archivePath', task.archive_path],
              ['strmPath', task.strm_path],
              ['createdAt', task.created_at],
              ['updatedAt', task.updated_at],
            ]"
            :key="item[0] || ''"
          >
            <dt>{{ t(`gdrive.${item[0]}`) }}</dt>
            <dd>{{ item[1] || '—' }}</dd>
          </template>
          <dt>{{ t('gdrive.sourcePolicy') }}</dt>
          <dd>{{ task.remove_source ? t('gdrive.removeSource') : t('gdrive.keepSource') }}</dd>
        </dl>
        <div class="d-flex align-center justify-space-between mt-6 mb-2">
          <h3 class="text-subtitle-1">{{ t('gdrive.history') }}</h3>
          <VBtn variant="text" :loading="loading" @click="loadHistory">{{ t('gdrive.refreshHistory') }}</VBtn>
        </div>
        <VAlert v-if="failed" type="error" variant="tonal">{{ t('gdrive.historyError') }}</VAlert>
        <VProgressLinear v-if="loading" indeterminate :aria-label="t('common.loading')" />
        <p v-if="!loading && !failed && !history.length" class="text-medium-emphasis">{{ t('gdrive.noHistory') }}</p>
        <VList v-if="!failed" bg-color="transparent">
          <VListItem v-for="record in history" :key="record.id" class="history-item">
            <VListItemTitle class="text-wrap"
              >{{ record.from_status ? `${statusText(record.from_status)} → ` : ''
              }}{{ statusText(record.to_status) }}</VListItemTitle
            >
            <VListItemSubtitle class="text-wrap"
              >{{ record.created_at }} · {{ statusText(record.to_stage) }} · {{ record.event_type }}</VListItemSubtitle
            >
            <p v-if="record.error_message" class="text-error text-body-2 mt-1">{{ record.error_message }}</p>
            <p v-if="record.detail" class="text-body-2 mt-1">{{ record.detail }}</p>
          </VListItem>
        </VList>
      </VCardText>
      <VCardActions
        ><VSpacer /><VBtn @click="emit('close')">{{ t('common.close') }}</VBtn></VCardActions
      >
    </VCard>
  </VDialog>
</template>

<style scoped>
.gdrive-paths dt {
  color: rgb(var(--v-theme-on-surface-variant));
  margin-block-start: 0.75rem;
  font-size: 0.8rem;
}
.gdrive-paths dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.history-item {
  border-inline-start: 2px solid rgba(var(--v-theme-primary), 0.3);
  margin-block: 0.5rem;
  overflow-wrap: anywhere;
}
</style>
