<script setup lang="ts">
import { gdriveRuleApi, type GdriveRule, type GdriveRuleConfig } from '@/api/gdrive'
import GdriveCategoryEditor from './GdriveCategoryEditor.vue'
import { useI18n } from 'vue-i18n'
const props = defineProps<{ rule?: GdriveRule; classify?: boolean }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const { t } = useI18n()
const form = reactive<GdriveRuleConfig>({
  name: props.rule?.name || '',
  media_type: props.rule?.media_type || 'auto',
  movie_directory: props.rule?.movie_directory || '{{ title }} ({{ year }})',
  movie_filename: props.rule?.movie_filename || '{{ title }} ({{ year }})',
  tv_directory: props.rule?.tv_directory || '{{ title }} ({{ year }})/Season {{ season }}',
  tv_filename: props.rule?.tv_filename || '{{ title }} - {{ season_episode }}',
  categories: [...(props.rule?.categories || ['media_type'])],
  category_policy: props.rule?.category_policy
    ? structuredClone(toRaw(props.rule.category_policy))
    : props.classify
      ? {
          movie: [],
          tv: [],
          unmatched: 'skip',
          fallback: '未分类',
          path_mode: 'relative',
        }
      : null,
})
const classification = ref(!!form.category_policy)
const categoryDraft = ref(form.category_policy)
const sampleJson = ref('{}')
watch(classification, enabled => {
  if (!enabled) categoryDraft.value = form.category_policy
  form.category_policy = enabled
    ? categoryDraft.value || { movie: [], tv: [], unmatched: 'skip', fallback: '未分类', path_mode: 'relative' }
    : null
})
function cleanForm(): GdriveRuleConfig {
  const clean = (rules: NonNullable<GdriveRuleConfig['category_policy']>['movie']) =>
    rules.map(rule => ({
      ...rule,
      conditions: Object.fromEntries(Object.entries(rule.conditions).filter(([, value]) => value.trim())),
    }))
  return {
    ...form,
    category_policy: form.category_policy
      ? {
          ...form.category_policy,
          movie: clean(form.category_policy.movie),
          tv: clean(form.category_policy.tv),
        }
      : null,
  }
}
const media = ref('movie')
const filename = ref('Example.Movie.2024.2160p.H265.mkv')
const preview = ref('')
const previewError = ref('')
const error = ref('')
const saving = ref(false)
const uncertain = ref(false)
const previewing = ref(false)
const variables = [
  'title',
  'year',
  'tmdb_id',
  'season',
  'episode',
  'season_episode',
  'resolution',
  'video_codec',
  'audio_codec',
  'team',
  'original_name',
  'media_type',
]
const categories = computed(() =>
  ['media_type', 'year', 'resolution', 'video_codec', 'team'].map(value => ({
    value,
    title: t(`gdrive.studio.variables.${value}`),
  })),
)
const types = computed(() => ['auto', 'movie', 'tv'].map(value => ({ value, title: t(`gdrive.studio.${value}`) })))
let controller: AbortController | undefined
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
const field = ref<'movie_directory' | 'movie_filename' | 'tv_directory' | 'tv_filename'>('movie_directory')
function insert(variable: string) {
  form[field.value] += `{{ ${variable} }}`
}
async function renderPreview() {
  controller?.abort()
  const request = new AbortController()
  controller = request
  preview.value = ''
  previewError.value = ''
  previewing.value = true
  try {
    const sample = JSON.parse(sampleJson.value)
    if (!sample || typeof sample !== 'object' || Array.isArray(sample)) throw new Error('sample')
    const response = await gdriveRuleApi.preview(
      { ...cleanForm(), name: form.name.trim() || t('gdrive.studio.newRule') },
      filename.value,
      request.signal,
      { ...sample, media_type: media.value },
    )
    if (request.signal.aborted) return
    if (response.success) preview.value = response.data?.path || ''
    else previewError.value = response.message || t('gdrive.directory.operationFailed')
  } catch {
    if (!request.signal.aborted) previewError.value = t('gdrive.studio.previewError')
  } finally {
    if (!request.signal.aborted) previewing.value = false
  }
}
watch(
  [form, filename, sampleJson, media],
  () => {
    controller?.abort()
    preview.value = ''
    clearTimeout(timer)
    timer = setTimeout(() => void renderPreview(), 350)
  },
  { deep: true },
)
watch(media, value => {
  filename.value = value === 'tv' ? 'Example.Show.2024.S01E02.1080p.H264.mkv' : 'Example.Movie.2024.2160p.H265.mkv'
  field.value = value === 'tv' ? 'tv_directory' : 'movie_directory'
})
async function save() {
  if (saving.value || uncertain.value || !form.name.trim()) return
  saving.value = true
  error.value = ''
  try {
    const response = await gdriveRuleApi.save(cleanForm(), props.rule?.id)
    if (disposed) return
    if (response.success) emit('saved')
    else error.value = response.message || t('gdrive.directory.operationFailed')
  } catch {
    if (!disposed) {
      uncertain.value = true
      error.value = t('gdrive.directory.uncertain')
    }
  } finally {
    if (!disposed) saving.value = false
  }
}
onMounted(renderPreview)
onUnmounted(() => {
  disposed = true
  controller?.abort()
  clearTimeout(timer)
})
</script>

<template>
  <VDialog
    :model-value="true"
    max-width="1120"
    scrollable
    :persistent="saving"
    :aria-label="t(rule ? 'gdrive.studio.editRule' : 'gdrive.studio.newRule')"
    @update:model-value="!saving && emit('close')"
  >
    <VCard>
      <VCardTitle class="pa-5">{{ t(rule ? 'gdrive.studio.editRule' : 'gdrive.studio.newRule') }}</VCardTitle>
      <VCardText>
        <VAlert type="info" variant="tonal" class="mb-5">{{ t('gdrive.studio.localMetadata') }}</VAlert>
        <div class="rule-layout" :class="{ 'category-enabled': classification }">
          <fieldset :disabled="saving || uncertain" class="rule-fields">
            <VTextField
              v-model="form.name"
              :label="t('gdrive.studio.ruleName')"
              :mobile-layout="false"
              maxlength="100"
            />
            <VSelect v-model="form.media_type" :items="types" :label="t('gdrive.studio.mediaType')" />
            <VTabs v-model="media" class="mb-2"
              ><VTab value="movie">{{ t('gdrive.studio.movie') }}</VTab
              ><VTab value="tv">{{ t('gdrive.studio.tv') }}</VTab></VTabs
            >
            <template v-if="media === 'movie'">
              <VTextField
                v-model="form.movie_directory"
                :label="t('gdrive.studio.directoryTemplate')"
                :mobile-layout="false"
                @focus="field = 'movie_directory'"
              />
              <VTextField
                v-model="form.movie_filename"
                :label="t('gdrive.studio.filenameTemplate')"
                :hint="t('gdrive.studio.extensionHint')"
                persistent-hint
                :mobile-layout="false"
                @focus="field = 'movie_filename'"
              />
            </template>
            <template v-else>
              <VTextField
                v-model="form.tv_directory"
                :label="t('gdrive.studio.directoryTemplate')"
                :mobile-layout="false"
                @focus="field = 'tv_directory'"
              />
              <VTextField
                v-model="form.tv_filename"
                :label="t('gdrive.studio.filenameTemplate')"
                :hint="t('gdrive.studio.extensionHint')"
                persistent-hint
                :mobile-layout="false"
                @focus="field = 'tv_filename'"
              />
            </template>
            <p class="text-subtitle-2">{{ t('gdrive.studio.variablesTitle') }}</p>
            <div class="d-flex flex-wrap ga-1">
              <VChip
                v-for="variable in variables"
                :key="variable"
                size="small"
                variant="tonal"
                @click="insert(variable)"
                >{{ t(`gdrive.studio.variables.${variable}`) }}</VChip
              >
            </div>
            <VSelect
              v-if="!classification"
              v-model="form.categories"
              :items="categories"
              multiple
              chips
              closable-chips
              :label="t('gdrive.studio.categories')"
              :hint="t('gdrive.studio.categoryHint')"
              persistent-hint
            />
            <div v-if="!classification" class="d-flex flex-wrap ga-2">
              <VChip v-for="(category, index) in form.categories" :key="category"
                >{{ index + 1 }} · {{ t(`gdrive.studio.variables.${category}`) }}</VChip
              >
            </div>
            <VDivider class="my-2" />
            <VSwitch
              v-model="classification"
              :label="t('gdrive.classification.enableRule')"
              color="primary"
              hide-details
            />
            <p class="text-body-2">{{ t('gdrive.classification.ruleHint') }}</p>
            <template v-if="classification && form.category_policy">
              <GdriveCategoryEditor v-model="form.category_policy" />
              <VSelect
                v-model="form.category_policy.unmatched"
                :items="
                  ['skip', 'fallback', 'keep'].map(value => ({ value, title: t(`gdrive.classification.${value}`) }))
                "
                :label="t('gdrive.classification.unmatched')"
              />
              <VTextField
                v-if="form.category_policy.unmatched === 'fallback'"
                v-model="form.category_policy.fallback"
                :label="t('gdrive.classification.fallbackName')"
                :mobile-layout="false"
              />
            </template>
          </fieldset>
          <aside class="rule-preview pa-5">
            <div class="d-flex align-center ga-2 mb-4">
              <VIcon icon="mdi-file-tree-outline" color="primary" />
              <h3 class="text-subtitle-1">{{ t('gdrive.studio.structurePreview') }}</h3>
            </div>
            <VTextField v-model="filename" :label="t('gdrive.studio.sampleFilename')" :mobile-layout="false" />
            <VTextarea
              v-if="classification"
              v-model="sampleJson"
              :label="t('gdrive.classification.sampleJson')"
              :hint="t('gdrive.classification.sampleHint')"
              persistent-hint
              rows="6"
              class="mb-3"
            />
            <VProgressLinear v-if="previewing" indeterminate class="mb-3" />
            <VAlert v-if="previewError" type="warning" variant="tonal">{{ previewError }}</VAlert>
            <div v-if="preview" class="preview-tree">
              <p class="text-caption text-medium-emphasis mb-3">{{ t('gdrive.studio.targetRoot') }}</p>
              <p
                v-for="(part, index) in preview.split('/')"
                :key="index"
                :style="{ paddingInlineStart: `${Math.min(index, 5) * 0.8}rem` }"
              >
                <VIcon
                  :icon="index === preview.split('/').length - 1 ? 'mdi-file-video-outline' : 'mdi-folder-outline'"
                  size="18"
                  class="me-2"
                />{{ part }}
              </p>
            </div>
            <p class="text-caption text-medium-emphasis mt-4">{{ t('gdrive.studio.previewHint') }}</p>
          </aside>
        </div>
        <VAlert v-if="error" type="error" variant="tonal" class="mt-4">{{ error }}</VAlert>
      </VCardText>
      <VCardActions class="pa-4"
        ><VBtn :disabled="saving" @click="emit('close')">{{ t('common.cancel') }}</VBtn
        ><VSpacer /><VBtn
          variant="flat"
          color="primary"
          :loading="saving"
          :disabled="!form.name.trim() || uncertain"
          @click="save"
          >{{ t('common.save') }}</VBtn
        ></VCardActions
      >
    </VCard>
  </VDialog>
</template>

<style scoped>
.rule-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
  gap: 1.5rem;
}
.rule-fields {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  border: 0;
  padding: 0;
  min-inline-size: 0;
}
.rule-layout.category-enabled {
  grid-template-columns: minmax(0, 1fr);
}
.rule-preview {
  border-radius: 0.75rem;
  background: rgba(var(--v-theme-primary), 0.045);
  min-inline-size: 0;
  align-self: start;
}
.preview-tree {
  overflow-wrap: anywhere;
  font-family: monospace;
  line-height: 2;
}
@media (max-width: 800px) {
  .rule-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
