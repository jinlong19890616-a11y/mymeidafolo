<script setup lang="ts">
import { gdriveApi, gdriveBytes, type GdrivePreflight, type GdriveSettings } from '@/api/gdrive'
import { useI18n } from 'vue-i18n'

const emit = defineEmits<{ close: []; created: [id: number | undefined] }>()
const { t } = useI18n()
const source = ref('')
const archive = ref('')
const policy = ref<'inherit' | 'keep' | 'remove'>('inherit')
const settings = ref<GdriveSettings>()
const settingsLoading = ref(false)
const settingsError = ref(false)
const checking = ref(false)
const submitting = ref(false)
const uncertain = ref(false)
const error = ref('')
const result = ref<GdrivePreflight>()
let settingsController: AbortController | undefined
let checkController: AbortController | undefined
let disposed = false
const removesSource = computed(
  () => policy.value === 'remove' || (policy.value === 'inherit' && settings.value?.remove_source),
)
const paths = () => ({
  source_path: source.value.trim(),
  ...(archive.value.trim() ? { archive_path: archive.value.trim() } : {}),
})
const policyOptions = computed(() => [
  { title: t('gdrive.inherit'), value: 'inherit' },
  { title: t('gdrive.keepSource'), value: 'keep' },
  { title: t('gdrive.removeSource'), value: 'remove' },
])

async function loadSettings() {
  settingsController?.abort()
  const controller = new AbortController()
  settingsController = controller
  settingsLoading.value = true
  settingsError.value = false
  try {
    const value = await gdriveApi.settings(controller.signal)
    if (!controller.signal.aborted) settings.value = value
  } catch {
    if (!controller.signal.aborted) settingsError.value = true
  } finally {
    if (!controller.signal.aborted) settingsLoading.value = false
  }
}

watch(
  [source, archive],
  () => {
    checkController?.abort()
    checking.value = false
    result.value = undefined
    if (!uncertain.value) error.value = ''
  },
  { flush: 'sync' },
)

async function preflight() {
  if (!source.value.trim() || checking.value || submitting.value || !settings.value?.enabled || uncertain.value) return
  const controller = new AbortController()
  checkController = controller
  checking.value = true
  error.value = ''
  result.value = undefined
  try {
    const value = await gdriveApi.preflight(paths(), controller.signal)
    if (!controller.signal.aborted) result.value = value
  } catch {
    if (!controller.signal.aborted) error.value = t('gdrive.preflightError')
  } finally {
    if (!controller.signal.aborted) checking.value = false
  }
}

async function create() {
  if (!result.value?.ready || !settings.value?.enabled || submitting.value || checking.value || uncertain.value) return
  submitting.value = true
  error.value = ''
  try {
    const response = await gdriveApi.create({
      ...paths(),
      ...(policy.value === 'inherit' ? {} : { remove_source: policy.value === 'remove' }),
    })
    if (disposed) return
    if (response.success) emit('created', response.data?.id)
    else error.value = response.message || t('gdrive.createError')
  } catch {
    if (!disposed) {
      uncertain.value = true
      error.value = t('gdrive.createUncertain')
    }
  } finally {
    if (!disposed) submitting.value = false
  }
}

onMounted(loadSettings)
onUnmounted(() => {
  disposed = true
  settingsController?.abort()
  checkController?.abort()
})
</script>

<template>
  <VDialog
    :model-value="true"
    :aria-label="t('gdrive.newTask')"
    max-width="720"
    :persistent="submitting"
    @update:model-value="!submitting && emit('close')"
  >
    <VCard :title="t('gdrive.newTask')">
      <VCardText class="d-flex flex-column ga-4">
        <VAlert type="info" variant="tonal">{{ t('gdrive.createHint') }}</VAlert>
        <VProgressLinear v-if="settingsLoading" indeterminate :aria-label="t('common.loading')" />
        <VAlert v-if="settingsError" type="error" variant="tonal">
          {{ t('gdrive.settingsError') }}
          <VBtn variant="text" @click="loadSettings">{{ t('gdrive.retry') }}</VBtn>
        </VAlert>
        <VAlert v-if="settings && !settings.enabled" type="warning" variant="tonal">{{ t('gdrive.disabled') }}</VAlert>
        <VTextField
          v-model="source"
          :label="t('gdrive.sourcePath')"
          :hint="t('gdrive.sourceHint')"
          persistent-hint
          :disabled="submitting"
        />
        <VTextField
          v-model="archive"
          :label="t('gdrive.archiveOverride')"
          :hint="t('gdrive.archiveHint')"
          persistent-hint
          :disabled="submitting"
        />
        <VSelect v-model="policy" :items="policyOptions" :label="t('gdrive.sourcePolicy')" :disabled="submitting" />
        <VAlert v-if="settings" :type="removesSource ? 'warning' : 'info'" variant="tonal">
          {{ removesSource ? t('gdrive.willRemove') : t('gdrive.willKeep') }}
        </VAlert>
        <VAlert v-if="error" type="error" role="alert" variant="tonal">{{ error }}</VAlert>
        <section v-if="result" :aria-label="t('gdrive.preflightResult')">
          <VAlert :type="result.ready ? 'success' : 'warning'" variant="tonal" class="mb-3">
            {{ result.ready ? t('gdrive.ready') : t('gdrive.notReady') }}
          </VAlert>
          <VList lines="two" bg-color="transparent">
            <VListItem
              v-for="(check, index) in result.checks"
              :key="`${check.name}-${index}`"
              :prepend-icon="check.passed ? 'mdi-check-circle-outline' : 'mdi-alert-circle-outline'"
              :title="check.message"
              :subtitle="check.name"
            />
          </VList>
          <dl class="gdrive-paths">
            <dt>{{ t('gdrive.mountPath') }}</dt>
            <dd>{{ result.mount_path }}</dd>
            <dt>{{ t('gdrive.archivePath') }}</dt>
            <dd>{{ result.archive_path || '—' }}</dd>
            <dt>{{ t('gdrive.strmPath') }}</dt>
            <dd>{{ result.strm_path || '—' }}</dd>
            <dt>{{ t('gdrive.size') }}</dt>
            <dd>{{ result.source_size === null ? '—' : gdriveBytes(result.source_size) }}</dd>
          </dl>
          <p class="text-caption text-medium-emphasis mt-3">{{ t('gdrive.preflightHint') }}</p>
        </section>
      </VCardText>
      <VCardActions class="flex-wrap pa-4 ga-2">
        <VBtn :disabled="submitting" @click="emit('close')">{{ t('common.cancel') }}</VBtn>
        <VSpacer />
        <VBtn
          variant="tonal"
          :loading="checking"
          :disabled="!source.trim() || submitting || !settings?.enabled || uncertain"
          @click="preflight"
          >{{ t('gdrive.preflight') }}</VBtn
        >
        <VBtn
          color="primary"
          variant="flat"
          :loading="submitting"
          :disabled="!result?.ready || !settings?.enabled || checking || uncertain"
          @click="create"
          >{{ t('gdrive.createAndStart') }}</VBtn
        >
      </VCardActions>
    </VCard>
  </VDialog>
</template>

<style scoped>
.gdrive-paths dt {
  color: rgba(var(--v-theme-on-surface), 0.7);
  margin-block-start: 0.75rem;
  font-size: 0.8rem;
}
.gdrive-paths dd {
  margin: 0;
  overflow-wrap: anywhere;
}
</style>
