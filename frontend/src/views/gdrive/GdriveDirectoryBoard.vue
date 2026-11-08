<script setup lang="ts">
import {
  gdriveApi,
  gdriveBytes,
  gdriveDirectoryApi,
  gdriveUploadApi,
  type GdriveDirectory,
  type GdriveScan,
  type GdriveScanDetail,
} from '@/api/gdrive'
import { useUserStore } from '@/stores'
import { useI18n } from 'vue-i18n'
import GdriveDirectoryDialog from './GdriveDirectoryDialog.vue'
import GdriveSettingsDialog from './GdriveSettingsDialog.vue'
import GdriveFlow from './GdriveFlow.vue'
import GdriveDetailDialog from './GdriveDetailDialog.vue'
import { type GdriveTask } from '@/api/gdrive'

withDefaults(defineProps<{ mode?: 'tasks' | 'history' | 'all' }>(), { mode: 'all' })

const { t, te } = useI18n()
const user = useUserStore()
const directories = ref<GdriveDirectory[]>([])
const scans = ref<GdriveScan[]>([])
const enabled = ref(false)
const uploadEnabled = ref(false)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const loadError = ref(false)
const loaded = ref(false)
const editor = ref(false)
const settingsOpen = ref(false)
const editing = ref<GdriveDirectory>()
const detail = ref<GdriveScanDetail>()
const selectedTask = ref<GdriveTask>()
const fileRequest = new AbortController()
async function showFile(id: number) {
  try {
    const value = await gdriveApi.get(id, fileRequest.signal)
    if (!disposed) selectedTask.value = value
  } catch {
    error.value = t('gdrive.listError')
  }
}
const selectedId = ref<number>()
const confirm = ref<{ task: GdriveDirectory; action: 'run' | 'delete' }>()
const search = ref<string | null>('')
let controller: AbortController | undefined
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
let refreshPending = false
const filtered = computed(() =>
  directories.value.filter(value =>
    `${value.name} ${value.source_path} ${value.archive_path}`
      .toLowerCase()
      .includes((search.value || '').toLowerCase()),
  ),
)
const statusText = (value: string) =>
  value === 'waiting'
    ? t('gdrive.classification.waiting')
    : te(`gdrive.directory.status.${value}`)
      ? t(`gdrive.directory.status.${value}`)
      : value
function reasonText(value: string) {
  if (value === 'nonrecursive') return t('gdrive.classification.nonrecursive')
  return value.startsWith('error:')
    ? value.slice(7)
    : te(`gdrive.directory.reason.${value}`)
      ? t(`gdrive.directory.reason.${value}`)
      : value
}
function schedule() {
  clearTimeout(timer)
  if (!disposed && !document.hidden) timer = setTimeout(() => void refresh(), 5000)
}
async function refresh() {
  if (disposed) return
  if (loading.value) {
    refreshPending = true
    return
  }
  clearTimeout(timer)
  loading.value = true
  const request = new AbortController()
  controller = request
  try {
    const [tasks, history, settings] = await Promise.all([
      gdriveDirectoryApi.list(request.signal),
      gdriveDirectoryApi.scans(request.signal),
      gdriveApi.settings(request.signal),
    ])
    if (request.signal.aborted) return
    directories.value = tasks
    scans.value = history
    enabled.value = settings.enabled
    if (tasks.some(task => task.upload_target)) {
      const upload = await gdriveUploadApi.settings(request.signal)
      if (request.signal.aborted) return
      uploadEnabled.value = !!upload.success && !!upload.data?.enabled
    } else uploadEnabled.value = false
    loaded.value = true
    loadError.value = false
    if (selectedId.value) {
      const value = await gdriveDirectoryApi.detail(selectedId.value, request.signal)
      if (!request.signal.aborted && selectedId.value === value.scan.id) detail.value = value
    }
    if (selectedTask.value && !['completed', 'failed', 'cancelled'].includes(selectedTask.value.status)) {
      const task = await gdriveApi.get(selectedTask.value.id, request.signal)
      if (!request.signal.aborted && selectedTask.value?.id === task.id) selectedTask.value = task
    }
  } catch {
    if (!request.signal.aborted) loadError.value = true
  } finally {
    if (!request.signal.aborted) {
      loading.value = false
      if (refreshPending) {
        refreshPending = false
        void refresh()
      } else schedule()
    }
  }
}
async function scan(task: GdriveDirectory, dryRun: boolean) {
  if (busy.value || !user.superUser) return
  busy.value = true
  error.value = ''
  try {
    const response = await gdriveDirectoryApi.scan(task.id, dryRun)
    if (disposed) return
    if (!response.success) error.value = response.message || t('gdrive.directory.operationFailed')
    else if (response.data?.id) {
      detail.value = undefined
      selectedId.value = response.data.id
    }
  } catch {
    if (!disposed) error.value = t('gdrive.directory.scanUncertain')
  } finally {
    if (!disposed) {
      busy.value = false
      confirm.value = undefined
      void refresh()
    }
  }
}
async function confirmAction() {
  const value = confirm.value
  if (!value || busy.value || !user.superUser) return
  if (value.action === 'run') return scan(value.task, false)
  busy.value = true
  try {
    const response = await gdriveDirectoryApi.remove(value.task.id)
    if (!disposed && !response.success) error.value = response.message || t('gdrive.directory.operationFailed')
  } catch {
    if (!disposed) error.value = t('gdrive.directory.uncertain')
  } finally {
    if (!disposed) {
      busy.value = false
      confirm.value = undefined
      void refresh()
    }
  }
}
async function showDetail(id: number) {
  selectedId.value = id
  detail.value = undefined
  await refresh()
}
function edit(task?: GdriveDirectory) {
  if (user.superUser) {
    editing.value = task
    editor.value = true
  }
}
function visibilityChanged() {
  if (document.hidden) clearTimeout(timer)
  else void refresh()
}
function manualRefresh() {
  error.value = ''
  void refresh()
}
function saved() {
  editor.value = false
  settingsOpen.value = false
  void refresh()
}
function closeDetail() {
  selectedId.value = undefined
  detail.value = undefined
}
onMounted(() => {
  void refresh()
  document.addEventListener('visibilitychange', visibilityChanged)
})
onUnmounted(() => {
  disposed = true
  controller?.abort()
  fileRequest.abort()
  clearTimeout(timer)
  document.removeEventListener('visibilitychange', visibilityChanged)
})
watch(
  () => user.superUser,
  value => {
    if (!value) {
      editor.value = false
      settingsOpen.value = false
      confirm.value = undefined
    }
  },
)
</script>

<template>
  <div>
    <div class="overview-grid mb-5">
      <VCard
        v-for="entry in [
          { label: 'overviewTasks', value: directories.length, icon: 'mdi-folder-outline' },
          {
            label: 'overviewActive',
            value: scans.filter(scan => scan.status === 'scanning' || scan.active > 0).length,
            icon: 'mdi-sync',
          },
          {
            label: 'overviewCompleted',
            value: scans.reduce((sum, scan) => sum + scan.completed, 0),
            icon: 'mdi-check-circle-outline',
          },
          {
            label: 'overviewFailed',
            value: scans.reduce((sum, scan) => sum + scan.failed, 0),
            icon: 'mdi-alert-circle-outline',
          },
        ]"
        :key="entry.label"
        variant="tonal"
      >
        <VCardText class="d-flex align-center ga-3"
          ><VIcon :icon="entry.icon" color="primary" />
          <div>
            <p class="text-caption text-medium-emphasis">{{ t(`gdrive.studio.${entry.label}`) }}</p>
            <strong class="text-h5">{{ entry.value }}</strong>
          </div></VCardText
        >
      </VCard>
    </div>
    <VCard v-if="mode !== 'history'" variant="flat" class="pa-5 mb-5 flow-card">
      <h2 class="text-subtitle-1 mb-1">{{ t('gdrive.studio.flow') }}</h2>
      <p class="text-caption text-medium-emphasis mb-4">{{ t('gdrive.studio.flowHint') }}</p>
      <GdriveFlow />
    </VCard>
    <div class="d-flex flex-wrap align-center ga-3 mb-4">
      <VTextField
        v-if="mode !== 'history'"
        v-model="search"
        :mobile-layout="false"
        :label="t('gdrive.directory.search')"
        hide-details
        class="flex-grow-1"
      />
      <VBtn variant="tonal" :loading="loading" @click="manualRefresh">{{ t('gdrive.refresh') }}</VBtn>
      <VBtn v-if="user.superUser && mode !== 'history'" color="primary" prepend-icon="mdi-plus" @click="edit()">{{
        t('gdrive.directory.new')
      }}</VBtn>
      <VBtn v-if="user.superUser && mode === 'all'" variant="tonal" @click="settingsOpen = true">{{
        t('gdrive.directory.settings')
      }}</VBtn>
    </div>
    <VAlert v-if="!enabled && !uploadEnabled" type="info" variant="tonal" class="mb-4">{{
      t('gdrive.directory.disabledHint')
    }}</VAlert>
    <VAlert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</VAlert>
    <VAlert v-if="loadError" type="error" variant="tonal" class="mb-4">{{ t('gdrive.directory.loadFailed') }}</VAlert>
    <template v-if="mode !== 'history'">
      <p class="text-caption text-medium-emphasis mb-4">{{ t('gdrive.directory.layoutHint') }}</p>
      <VCard v-if="loaded && !filtered.length" class="pa-8 text-center mb-5">{{
        t(directories.length ? 'gdrive.noMatches' : 'gdrive.directory.empty')
      }}</VCard>
      <div class="directory-grid mb-6">
        <VCard v-for="task in filtered" :key="task.id" :title="task.name">
          <VCardText>
            <div class="d-flex flex-wrap ga-2 mb-3">
              <VChip size="small" :color="task.enabled ? 'success' : 'secondary'">{{
                t(task.enabled ? 'gdrive.directory.enabled' : 'gdrive.directory.disabled')
              }}</VChip>
              <VChip size="small">{{
                t(task.schedule_enabled ? 'gdrive.directory.schedule' : 'gdrive.directory.manual')
              }}</VChip>
            </div>
            <dl class="directory-paths">
              <dt>{{ t('gdrive.directory.source') }}</dt>
              <dd>{{ task.source_path }}</dd>
              <dt>{{ t('gdrive.directory.target') }}</dt>
              <dd>
                {{
                  task.upload_target
                    ? `Google Drive · ${task.upload_target.path || '/'} · ${task.upload_target.account_ids.length} 个账号`
                    : task.archive_path
                }}
              </dd>
              <dt>{{ t('gdrive.classification.mode') }}</dt>
              <dd>{{ t(`gdrive.classification.${task.classification_mode || 'inherit'}`) }}</dd>
              <dt>{{ t('gdrive.classification.strmRoot') }}</dt>
              <dd>
                {{ task.strm_root || (task.upload_target ? '不生成 STRM' : t('gdrive.classification.sourceSide')) }}
              </dd>
              <dt>{{ t('gdrive.directory.next') }}</dt>
              <dd>{{ task.next_run_at || '—' }}</dd>
              <dt>{{ t('gdrive.directory.last') }}</dt>
              <dd>{{ task.last_scan_at || '—' }}</dd>
            </dl>
            <p class="text-caption mt-3">
              {{ task.extensions.join(', ') }} · {{ task.min_size_mb }} MiB · {{ task.settle_seconds }} s
            </p>
            <VChip size="small" variant="tonal" prepend-icon="mdi-file-tree-outline" class="mt-3">{{
              task.rule_id ? `${t('gdrive.studio.archiveRule')} #${task.rule_id}` : t('gdrive.studio.keepStructure')
            }}</VChip>
            <p class="text-caption mt-2">{{ t(task.remove_source ? 'gdrive.removeSource' : 'gdrive.keepSource') }}</p>
          </VCardText>
          <VCardActions v-if="user.superUser" class="flex-wrap ga-1">
            <VBtn :disabled="busy" @click="edit(task)">{{ t('gdrive.directory.edit') }}</VBtn>
            <VBtn :disabled="busy" @click="scan(task, true)">{{ t('gdrive.directory.preview') }}</VBtn>
            <VBtn
              color="primary"
              :disabled="busy || !(task.upload_target ? uploadEnabled : enabled) || !task.enabled"
              @click="confirm = { task, action: 'run' }"
              >{{ t('gdrive.directory.run') }}</VBtn
            >
            <VBtn color="error" :disabled="busy || task.enabled" @click="confirm = { task, action: 'delete' }">{{
              t('gdrive.directory.delete')
            }}</VBtn>
          </VCardActions>
        </VCard>
      </div>
    </template>
    <template v-if="mode !== 'tasks'">
      <h2 class="text-h6 mb-3">{{ t('gdrive.directory.history') }}</h2>
      <p class="text-caption text-medium-emphasis mb-3">{{ t('gdrive.directory.historyHint') }}</p>
      <VCard v-if="!scans.length" class="pa-6 text-center">{{ t('gdrive.directory.noHistory') }}</VCard>
      <VCard v-for="entry in scans" :key="entry.id" class="mb-3">
        <VCardText class="d-flex flex-wrap align-center ga-3">
          <div class="flex-grow-1">
            <strong>#{{ entry.id }} {{ entry.directory_name }}</strong>
            <p class="text-caption mt-1">
              {{ entry.created_at }} UTC ·
              {{ t(entry.dry_run ? 'gdrive.directory.preview' : 'gdrive.directory.run') }} ·
              {{
                t(
                  entry.trigger === 'transfer'
                    ? 'gdrive.classification.transfer'
                    : entry.trigger === 'scheduled'
                      ? 'gdrive.directory.schedule'
                      : 'gdrive.directory.manual',
                )
              }}
            </p>
            <p class="text-body-2 mt-2">{{ t('gdrive.directory.counts', entry) }}</p>
            <p v-if="entry.error_message" class="text-error directory-paths mt-2">{{ entry.error_message }}</p>
          </div>
          <VChip :color="entry.status === 'failed' ? 'error' : entry.active ? 'primary' : 'secondary'">{{
            statusText(entry.status)
          }}</VChip>
          <VBtn
            variant="text"
            :aria-label="`${t('gdrive.directory.detail')} #${entry.id}`"
            @click="showDetail(entry.id)"
            >{{ t('gdrive.directory.detail') }}</VBtn
          >
        </VCardText>
      </VCard>
    </template>
    <GdriveDirectoryDialog v-if="editor" :task="editing" @close="editor = false" @saved="saved" />
    <GdriveSettingsDialog v-if="settingsOpen" @close="settingsOpen = false" @saved="saved" />
    <VDialog
      :model-value="!!confirm"
      :aria-label="t(confirm?.action === 'run' ? 'gdrive.directory.run' : 'gdrive.directory.delete')"
      max-width="560"
      :persistent="busy"
      @update:model-value="!busy && (confirm = undefined)"
    >
      <VCard v-if="confirm" :title="t(confirm.action === 'run' ? 'gdrive.directory.run' : 'gdrive.directory.delete')">
        <VCardText>
          <p>{{ confirm.task.name }}</p>
          <p class="mt-3">
            {{ t(confirm.action === 'run' ? 'gdrive.directory.runHint' : 'gdrive.directory.deleteHint') }}
          </p>
          <VAlert
            v-if="confirm.action === 'run'"
            :type="confirm.task.remove_source ? 'warning' : 'info'"
            variant="tonal"
            class="mt-4"
            >{{ t(confirm.task.remove_source ? 'gdrive.willRemove' : 'gdrive.willKeep') }}</VAlert
          >
        </VCardText>
        <VCardActions
          ><VBtn :disabled="busy" @click="confirm = undefined">{{ t('common.cancel') }}</VBtn
          ><VSpacer /><VBtn :loading="busy" color="primary" @click="confirmAction">{{
            t('gdrive.directory.confirm')
          }}</VBtn></VCardActions
        >
      </VCard>
    </VDialog>
    <VDialog
      :model-value="!!selectedId"
      :aria-label="t('gdrive.directory.detail')"
      max-width="1000"
      scrollable
      @update:model-value="closeDetail"
    >
      <VCard :title="t('gdrive.directory.detail')">
        <VCardText>
          <VProgressLinear v-if="!detail && loading" indeterminate />
          <VAlert v-if="loadError" type="error" variant="tonal">{{ t('gdrive.directory.loadFailed') }}</VAlert>
          <template v-if="detail">
            <p>#{{ detail.scan.id }} {{ detail.scan.directory_name }} · {{ statusText(detail.scan.status) }}</p>
            <div class="my-4"><GdriveFlow :scan="detail.scan" /></div>
            <div v-if="detail.config" class="snapshot pa-4 my-4">
              <h3 class="text-subtitle-2 mb-2">{{ t('gdrive.studio.snapshot') }}</h3>
              <p>{{ detail.config.source_path }} → {{ detail.config.archive_path }}</p>
              <p class="mt-2">
                {{ t('gdrive.classification.mode') }}:
                {{ t(`gdrive.classification.${detail.config.classification_mode || 'inherit'}`) }}
              </p>
              <p>
                {{ t('gdrive.classification.strmRoot') }}:
                {{ detail.config.strm_root || t('gdrive.classification.sourceSide') }}
              </p>
              <template
                v-if="
                  detail.config.classification_mode &&
                  detail.config.classification_mode !== 'inherit' &&
                  detail.config.category_policy
                "
              >
                <p>
                  {{ t('gdrive.classification.unmatched') }}:
                  {{ t(`gdrive.classification.${detail.config.category_policy.unmatched}`) }}
                  {{
                    detail.config.category_policy.unmatched === 'fallback' ? detail.config.category_policy.fallback : ''
                  }}
                </p>
                <details class="mt-2">
                  <summary>{{ t('gdrive.classification.mode') }}</summary>
                  <div v-for="type in ['movie', 'tv'] as const" :key="type" class="mt-2">
                    <strong>{{ t(`mediaType.${type}`) }}</strong>
                    <p v-for="(rule, index) in detail.config.category_policy[type]" :key="rule.name">
                      {{ index + 1 }} · {{ rule.name }} ·
                      {{
                        Object.entries(rule.conditions)
                          .map(([key, value]) => `${key}: ${value}`)
                          .join(' / ') || t('gdrive.classification.catchAll')
                      }}
                    </p>
                  </div>
                </details>
              </template>
              <p class="text-caption mt-2">
                {{ detail.rule ? `${detail.rule.name} · v${detail.rule.revision}` : t('gdrive.studio.keepStructure') }}
                · {{ t(detail.config.remove_source ? 'gdrive.removeSource' : 'gdrive.keepSource') }}
              </p>
            </div>
            <p class="text-caption mt-2">{{ t('gdrive.directory.itemsHint', { total: detail.total_items }) }}</p>
            <VAlert v-if="detail.scan.error_message" type="error" variant="tonal" class="mt-3">{{
              detail.scan.error_message
            }}</VAlert>
            <article v-for="item in detail.items" :key="item.id" class="scan-item">
              <p>{{ item.source_path }}</p>
              <p class="text-medium-emphasis mt-1">→ {{ item.archive_path }}</p>
              <p class="text-caption mt-2">
                {{ reasonText(item.reason) }} · {{ gdriveBytes(item.size) }}
                <span v-if="item.task_id">· #{{ item.task_id }} · {{ t(`gdrive.status.${item.status}`) }}</span>
              </p>
              <p v-if="item.error_message" class="text-error mt-2">{{ item.error_message }}</p>
              <div v-if="item.task_id" class="mt-3">
                <GdriveFlow :stage="item.stage" :status="item.status" /><VProgressLinear
                  v-if="item.status === 'uploading'"
                  :model-value="item.progress || 0"
                  class="mt-2"
                /><VBtn variant="text" class="mt-2" @click="showFile(item.task_id)">{{
                  t('gdrive.studio.fileDetails')
                }}</VBtn>
              </div>
            </article>
          </template>
        </VCardText>
        <VCardActions
          ><VBtn @click="closeDetail">{{ t('common.close') }}</VBtn
          ><VSpacer /><VBtn :loading="loading" @click="refresh">{{ t('gdrive.refresh') }}</VBtn></VCardActions
        >
      </VCard>
    </VDialog>
    <GdriveDetailDialog v-if="selectedTask" :task="selectedTask" @close="selectedTask = undefined" />
  </div>
</template>

<style scoped>
.directory-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr));
  gap: 1rem;
}
.overview-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1rem;
}
.flow-card,
.snapshot {
  background: rgba(var(--v-theme-primary), 0.035);
  border-radius: 0.75rem;
  overflow-wrap: anywhere;
}
@media (max-width: 700px) {
  .overview-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.directory-paths,
.scan-item {
  overflow-wrap: anywhere;
}
.directory-paths dt {
  color: rgba(var(--v-theme-on-surface), 0.7);
  font-size: 0.8rem;
  margin-block-start: 0.75rem;
}
.directory-paths dd {
  margin: 0;
}
.scan-item {
  border-block-start: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  padding-block: 1rem;
}
</style>
