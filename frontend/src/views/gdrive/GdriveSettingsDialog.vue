<script setup lang="ts">
import { gdriveApi, gdriveDirectoryApi, type GdriveSettings } from '@/api/gdrive'
import { useI18n } from 'vue-i18n'
import GdrivePathField from './GdrivePathField.vue'

const emit = defineEmits<{ close: []; saved: [] }>()
const { t } = useI18n()
const form = ref<GdriveSettings>()
const saving = ref(false)
const uncertain = ref(false)
const error = ref('')
const controller = new AbortController()
let disposed = false
async function load() {
  error.value = ''
  try {
    form.value = await gdriveApi.settings(controller.signal)
  } catch {
    if (!disposed) error.value = t('gdrive.settingsError')
  }
}
async function save() {
  if (!form.value || saving.value || uncertain.value) return
  saving.value = true
  error.value = ''
  try {
    const result = await gdriveDirectoryApi.settings(form.value)
    if (disposed) return
    if (result.success) emit('saved')
    else error.value = result.message || t('gdrive.directory.operationFailed')
  } catch {
    if (!disposed) {
      uncertain.value = true
      error.value = t('gdrive.directory.uncertain')
    }
  } finally {
    if (!disposed) saving.value = false
  }
}
onMounted(load)
onUnmounted(() => {
  disposed = true
  controller.abort()
})
</script>

<template>
  <VDialog
    :model-value="true"
    :aria-label="t('gdrive.directory.settings')"
    max-width="700"
    :persistent="saving"
    @update:model-value="!saving && emit('close')"
  >
    <VCard :title="t('gdrive.directory.settings')">
      <VCardText>
        <VAlert type="info" variant="tonal" class="mb-4">{{ t('gdrive.directory.settingsHint') }}</VAlert>
        <div v-if="form" class="d-flex flex-column ga-4">
          <VSwitch
            v-model="form.enabled"
            :disabled="saving || uncertain"
            :label="t('gdrive.directory.globalEnabled')"
            hide-details
            color="primary"
          />
          <GdrivePathField v-model="form.mount_path" :disabled="saving || uncertain" :label="t('gdrive.mountPath')" />
          <GdrivePathField
            v-model="form.archive_path"
            :disabled="saving || uncertain"
            :label="t('gdrive.archivePath')"
          />
          <VTextField
            v-model="form.strm_path_prefix"
            :disabled="saving || uncertain"
            :label="t('gdrive.directory.strmPrefix')"
          />
        </div>
        <VAlert v-if="error" type="error" variant="tonal" class="mt-4">{{ error }}</VAlert>
        <VBtn v-if="!form && error" @click="load">{{ t('gdrive.retry') }}</VBtn>
      </VCardText>
      <VCardActions>
        <VBtn :disabled="saving" @click="emit('close')">{{ t('common.cancel') }}</VBtn
        ><VSpacer />
        <VBtn color="primary" :disabled="!form || uncertain" :loading="saving" @click="save">{{
          t('common.save')
        }}</VBtn>
      </VCardActions>
    </VCard>
  </VDialog>
</template>
