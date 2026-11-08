<script setup lang="ts">
import {
  gdriveApi,
  gdriveBytes,
  gdriveTaskTitle,
  isGdriveTaskActive,
  GDRIVE_STATUSES,
  type GdriveTask,
} from '@/api/gdrive'
import { useUserStore } from '@/stores'
import { buildUserPermissionContext, hasPermission } from '@/utils/permission'
import GdriveCreateDialog from '@/views/gdrive/GdriveCreateDialog.vue'
import GdriveDetailDialog from '@/views/gdrive/GdriveDetailDialog.vue'
import GdriveDirectoryBoard from '@/views/gdrive/GdriveDirectoryBoard.vue'
import GdriveRules from '@/views/gdrive/GdriveRules.vue'
import GdriveSettingsDialog from '@/views/gdrive/GdriveSettingsDialog.vue'
import GdriveUpload from '@/views/gdrive/GdriveUpload.vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const user = useUserStore()
const canManage = computed(() => hasPermission(buildUserPermissionContext(user.superUser, user.permissions), 'manage'))
const tasks = ref<GdriveTask[]>([])
const loading = ref(false)
const failed = ref(false)
const loaded = ref(false)
const search = ref<string | null>('')
const status = ref('all')
const createOpen = ref(false)
const selected = ref<GdriveTask>()
const created = ref(false)
const tab = ref('directories')
const settingsOpen = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let controller: AbortController | undefined
let disposed = false
let refreshRequested = false
const statuses = computed(() => [
  { title: t('common.all'), value: 'all' },
  ...GDRIVE_STATUSES.map(value => ({ title: statusText(value), value })),
])
const filtered = computed(() =>
  tasks.value.filter(
    task =>
      (status.value === 'all' || task.status === status.value) &&
      `${gdriveTaskTitle(task)} ${task.src_path} ${task.archive_path || ''}`
        .toLowerCase()
        .includes((search.value || '').trim().toLowerCase()),
  ),
)
const statusText = (value: string) =>
  GDRIVE_STATUSES.some(item => item === value) ? t(`gdrive.status.${value}`) : value
const statusColor = (value: string) => {
  const colors: Record<string, string> = {
    completed: 'success',
    failed: 'error',
    cancelled: 'secondary',
    pending: 'secondary',
  }
  return colors[value] || 'primary'
}

function schedule() {
  clearTimeout(timer)
  if (!disposed && canManage.value && !document.hidden && tasks.value.some(isGdriveTaskActive)) {
    timer = setTimeout(() => void refresh(), 5000)
  }
}

async function refresh() {
  if (loading.value || !canManage.value || disposed) return
  clearTimeout(timer)
  const request = new AbortController()
  controller = request
  loading.value = true
  failed.value = false
  try {
    const values = await gdriveApi.list(request.signal)
    if (request.signal.aborted) return
    tasks.value = values
    loaded.value = true
    if (selected.value) selected.value = values.find(task => task.id === selected.value?.id) || selected.value
  } catch {
    if (!request.signal.aborted) failed.value = true
  } finally {
    if (!request.signal.aborted) {
      loading.value = false
      if (refreshRequested) {
        refreshRequested = false
        void refresh()
      } else schedule()
    }
  }
}

function visibilityChanged() {
  if (document.hidden) clearTimeout(timer)
  else if (canManage.value) void refresh()
}

function taskCreated() {
  createOpen.value = false
  created.value = true
  if (loading.value) refreshRequested = true
  else void refresh()
}

watch(canManage, allowed => {
  if (allowed) void refresh()
  else {
    controller?.abort()
    clearTimeout(timer)
    loading.value = false
    refreshRequested = false
    loaded.value = false
    tasks.value = []
    selected.value = undefined
    createOpen.value = false
  }
})
onMounted(() => {
  document.addEventListener('visibilitychange', visibilityChanged)
  void refresh()
})
onUnmounted(() => {
  disposed = true
  controller?.abort()
  clearTimeout(timer)
  document.removeEventListener('visibilitychange', visibilityChanged)
})
</script>

<template>
  <section class="gdrive-page">
    <VAlert v-if="!canManage" type="warning" variant="tonal">{{ t('gdrive.noPermission') }}</VAlert>
    <template v-else>
      <div class="d-flex align-center flex-wrap ga-3 mb-5">
        <div class="flex-grow-1">
          <h1 class="text-h5">{{ t('gdrive.title') }}</h1>
          <p class="text-body-2 text-medium-emphasis mt-1">{{ t('gdrive.description') }}</p>
        </div>
        <VBtn v-if="tab === 'files'" variant="tonal" prepend-icon="mdi-refresh" :loading="loading" @click="refresh">{{
          t('gdrive.refresh')
        }}</VBtn>
        <VBtn v-if="tab === 'files'" color="primary" prepend-icon="mdi-plus" @click="createOpen = true">{{
          t('gdrive.newTask')
        }}</VBtn>
        <VBtn v-if="user.superUser" variant="tonal" prepend-icon="mdi-cog-outline" @click="settingsOpen = true">{{
          t('gdrive.studio.configuration')
        }}</VBtn>
      </div>
      <VTabs v-model="tab" class="mb-5" show-arrows>
        <VTab value="upload" prepend-icon="mdi-cloud-upload-outline">直接上传</VTab>
        <VTab value="directories" prepend-icon="mdi-folder-sync-outline">{{ t('gdrive.studio.tasks') }}</VTab>
        <VTab value="records" prepend-icon="mdi-history">{{ t('gdrive.studio.records') }}</VTab>
        <VTab value="rules" prepend-icon="mdi-file-tree-outline">{{ t('gdrive.studio.rules') }}</VTab>
        <VTab value="files" prepend-icon="mdi-file-video-outline">{{ t('gdrive.directory.files') }}</VTab>
      </VTabs>
      <GdriveDirectoryBoard
        v-if="tab === 'directories' || tab === 'records'"
        :mode="tab === 'records' ? 'history' : 'tasks'"
      />
      <GdriveRules v-if="tab === 'rules'" />
      <GdriveUpload v-if="tab === 'upload'" />
      <GdriveSettingsDialog
        v-if="settingsOpen && user.superUser"
        @close="settingsOpen = false"
        @saved="settingsOpen = false"
      />
      <div v-show="tab === 'files'">
        <VAlert v-if="created" type="success" variant="tonal" closable class="mb-4" @click:close="created = false">{{
          t('gdrive.created')
        }}</VAlert>
        <VAlert v-if="failed" type="error" variant="tonal" class="mb-4">{{ t('gdrive.listError') }}</VAlert>
        <VCard>
          <VCardText class="d-flex flex-wrap ga-3 align-center">
            <VTextField
              v-model="search"
              :label="t('gdrive.search')"
              prepend-inner-icon="mdi-magnify"
              hide-details
              clearable
              class="gdrive-search"
              @click:clear="search = ''"
            />
            <VSelect
              v-model="status"
              :items="statuses"
              :label="t('gdrive.statusLabel')"
              hide-details
              class="gdrive-status"
            />
          </VCardText>
          <p class="text-caption text-medium-emphasis px-6 pb-3">{{ t('gdrive.listHint') }}</p>
          <VProgressLinear v-if="loading" indeterminate :aria-label="t('common.loading')" />
          <div v-if="loaded && !filtered.length" class="pa-8 text-center">
            <VIcon icon="mdi-folder-outline" size="40" class="mb-3 text-medium-emphasis" />
            <p>{{ tasks.length ? t('gdrive.noMatches') : t('gdrive.empty') }}</p>
          </div>
          <div v-if="filtered.length" class="gdrive-tasks">
            <article v-for="task in filtered" :key="task.id" class="gdrive-task">
              <div class="gdrive-identity">
                <div class="d-flex align-center ga-2 mb-1">
                  <span class="text-caption text-medium-emphasis">#{{ task.id }}</span>
                  <VChip :color="statusColor(task.status)" size="small" variant="tonal">{{
                    statusText(task.status)
                  }}</VChip>
                </div>
                <h2 class="text-subtitle-1 text-break">{{ gdriveTaskTitle(task) }}</h2>
                <p class="text-caption text-medium-emphasis gdrive-path">{{ task.src_path }}</p>
                <p v-if="task.error_message" class="text-error text-body-2 gdrive-path mt-1">
                  {{ task.error_message }}
                </p>
              </div>
              <div class="gdrive-progress">
                <p class="text-body-2 mb-2">{{ statusText(task.stage) }} · {{ task.progress }}%</p>
                <VProgressLinear
                  :model-value="task.progress"
                  :color="statusColor(task.status)"
                  height="6"
                  rounded
                  :aria-label="t('gdrive.progress')"
                />
                <p class="text-caption text-medium-emphasis mt-1">
                  {{ gdriveBytes(task.uploaded_size) }} / {{ gdriveBytes(task.source_size) }}
                </p>
              </div>
              <div class="text-caption text-medium-emphasis">
                <p>{{ t('gdrive.updatedAt') }}: {{ task.updated_at }}</p>
                <p>{{ t('gdrive.retries') }}: {{ task.retry_count }}</p>
              </div>
              <VBtn variant="text" :aria-label="`${t('gdrive.details')} #${task.id}`" @click="selected = task">{{
                t('gdrive.details')
              }}</VBtn>
            </article>
          </div>
        </VCard>
        <p class="text-caption text-medium-emphasis mt-3">{{ t('gdrive.progressHint') }}</p>
      </div>
      <GdriveCreateDialog v-if="createOpen" @close="createOpen = false" @created="taskCreated" />
      <GdriveDetailDialog v-if="selected" :task="selected" @close="selected = undefined" />
    </template>
  </section>
</template>

<style scoped>
.gdrive-search {
  flex: 1 1 16rem;
}
.gdrive-status {
  flex: 0 1 13rem;
  min-width: 10rem;
}
.gdrive-task {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(9rem, 1fr) minmax(8rem, 1fr) auto;
  gap: 1.5rem;
  align-items: center;
  padding: 1.25rem 1.5rem;
  border-block-start: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.gdrive-path {
  overflow-wrap: anywhere;
}
@media (max-width: 800px) {
  .gdrive-task {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 1rem;
  }
  .gdrive-identity,
  .gdrive-progress {
    grid-column: 1 / -1;
  }
}
</style>
