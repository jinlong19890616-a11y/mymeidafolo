<script setup lang="ts">
import api from '@/api'
import type { FileItem } from '@/api/types'
import { useI18n } from 'vue-i18n'

defineProps<{ modelValue: string; label: string; hint?: string; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const { t } = useI18n()
const open = ref(false)
const current = ref('/')
const entries = ref<FileItem[]>([])
const loading = ref(false)
const failed = ref(false)
let request: AbortController | undefined
async function browse(path: string) {
  request?.abort()
  const controller = new AbortController()
  request = controller
  loading.value = true
  failed.value = false
  try {
    const items = await api.post<never, FileItem[]>(
      '/storage/list',
      {
        storage: 'local',
        path,
        type: 'dir',
        name: path.split('/').filter(Boolean).pop() || '/',
      },
      { signal: controller.signal },
    )
    if (controller.signal.aborted) return
    current.value = path.replace(/\/$/, '') || '/'
    entries.value = items.filter(item => item.type === 'dir')
  } catch {
    if (!controller.signal.aborted) failed.value = true
  } finally {
    if (!controller.signal.aborted) loading.value = false
  }
}
function show(path: string) {
  open.value = true
  void browse(path.trim().startsWith('/') ? path.trim() : '/')
}
function selectDirectory() {
  emit('update:modelValue', current.value)
  open.value = false
}
const parent = computed(() => current.value.split('/').slice(0, -1).join('/') || '/')
onUnmounted(() => request?.abort())
</script>

<template>
  <div>
    <VTextField
      :model-value="modelValue"
      :label="label"
      :hint="hint"
      :persistent-hint="!!hint"
      :disabled="disabled"
      :mobile-layout="false"
      append-inner-icon="mdi-folder-open-outline"
      @update:model-value="emit('update:modelValue', $event)"
      @click:append-inner="show(modelValue)"
    />
    <VBtn
      size="small"
      variant="text"
      prepend-icon="mdi-folder-search-outline"
      :disabled="disabled"
      :aria-label="`${t('gdrive.studio.browse')} · ${label}`"
      @click="show(modelValue)"
      >{{ t('gdrive.studio.browse') }}</VBtn
    >
    <VDialog v-model="open" max-width="640" :aria-label="`${t('gdrive.studio.browse')} · ${label}`">
      <VCard :title="`${t('gdrive.studio.browse')} · ${label}`">
        <VCardText>
          <div class="d-flex align-center ga-2 mb-3">
            <VBtn
              icon="mdi-arrow-up"
              variant="tonal"
              :aria-label="t('gdrive.studio.parent')"
              :disabled="loading || current === '/'"
              @click="browse(parent)"
            />
            <code class="path-current">{{ current }}</code>
            <VBtn icon="mdi-home-outline" variant="text" :aria-label="t('gdrive.studio.root')" @click="browse('/')" />
          </div>
          <VProgressLinear v-if="loading" indeterminate />
          <VAlert v-if="failed" type="error" variant="tonal" class="mb-3"
            >{{ t('gdrive.studio.browseError') }}
            <VBtn variant="text" @click="browse(current)">{{ t('gdrive.retry') }}</VBtn>
          </VAlert>
          <VList class="path-list" :aria-label="label">
            <VListItem
              v-for="item in entries"
              :key="item.path"
              :title="item.name"
              prepend-icon="mdi-folder-outline"
              :disabled="loading"
              @click="browse(item.path)"
            />
            <VListItem v-if="!loading && !failed && !entries.length" :title="t('gdrive.studio.noDirectories')" />
          </VList>
        </VCardText>
        <VCardActions class="pa-4">
          <VBtn @click="open = false">{{ t('common.cancel') }}</VBtn
          ><VSpacer />
          <VBtn color="primary" variant="flat" :disabled="loading || failed" @click="selectDirectory">{{
            t('gdrive.studio.selectDirectory')
          }}</VBtn>
        </VCardActions>
      </VCard>
    </VDialog>
  </div>
</template>

<style scoped>
.path-current {
  overflow-wrap: anywhere;
  flex: 1;
  min-inline-size: 0;
}
.path-list {
  max-block-size: 20rem;
  overflow-y: auto;
}
</style>
