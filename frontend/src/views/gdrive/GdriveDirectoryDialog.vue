<script setup lang="ts">
import {
  gdriveDirectoryApi,
  type GdriveCategoryPolicy,
  type GdriveDirectory,
  type GdriveDirectoryConfig,
} from '@/api/gdrive'
import { useI18n } from 'vue-i18n'
import { gdriveRuleApi, type GdriveRule } from '@/api/gdrive'
import GdrivePathField from './GdrivePathField.vue'
import GdriveRuleDialog from './GdriveRuleDialog.vue'
import GdriveFlow from './GdriveFlow.vue'
import GdriveCategoryEditor from './GdriveCategoryEditor.vue'
import GdriveUploadTarget from './GdriveUploadTarget.vue'

const props = defineProps<{ task?: GdriveDirectory }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const { t } = useI18n()
const form = reactive<GdriveDirectoryConfig>({
  name: props.task?.name || '',
  source_path: props.task?.source_path || '',
  archive_path: props.task?.archive_path || '',
  enabled: props.task?.enabled || false,
  recursive: props.task?.recursive ?? true,
  remove_source: props.task?.remove_source ?? false,
  extensions: props.task?.extensions || ['.mkv', '.mp4', '.avi', '.mov', '.m2ts', '.ts'],
  ignore_keywords: props.task?.ignore_keywords || ['sample', 'trailer', 'bonus', '.tmp'],
  ignore_suffixes: props.task?.ignore_suffixes || [],
  min_size_mb: props.task?.min_size_mb ?? 0,
  settle_seconds: props.task?.settle_seconds ?? 300,
  schedule_enabled: props.task?.schedule_enabled || false,
  interval_minutes: props.task?.interval_minutes ?? 30,
  rule_id: props.task?.rule_id ?? null,
  classification_mode: props.task?.classification_mode || 'inherit',
  transfer_enabled: props.task?.transfer_enabled ?? true,
  strm_root: props.task?.strm_root || '',
  upload_target: props.task?.upload_target ? structuredClone(toRaw(props.task.upload_target)) : null,
})
const policy = ref<GdriveCategoryPolicy>(
  structuredClone(toRaw(props.task?.category_policy) || { movie: [], tv: [], unmatched: 'skip', fallback: '未分类' }),
)
const modeOptions = computed(() =>
  ['inherit', 'moviepilot', 'custom'].map(value => ({ title: t(`gdrive.classification.${value}`), value })),
)
const unmatchedOptions = computed(() =>
  ['skip', 'fallback', 'keep'].map(value => ({ title: t(`gdrive.classification.${value}`), value })),
)
const sample = reactive({
  filename: 'Example.Movie.2024.mkv',
  media_type: 'movie',
  original_language: 'zh',
  origin_country: 'CN',
  production_countries: 'CN',
  genre_ids: '16',
  year: '2024',
  title: '',
  include_keywords: '',
  actors: '',
  directors: '',
})
const categoryPreview = ref('')
const previewError = ref('')
const previewing = ref(false)
const frozenPolicy = ref<GdriveCategoryPolicy>()
function cleanPolicy(): GdriveCategoryPolicy {
  const clean = (rules: GdriveCategoryPolicy['movie']) =>
    rules.map(rule => ({
      ...rule,
      conditions: Object.fromEntries(Object.entries(rule.conditions).filter(([, value]) => value?.trim())),
    }))
  return { ...policy.value, movie: clean(policy.value.movie), tv: clean(policy.value.tv) }
}
async function previewCategory() {
  if (previewing.value) return
  previewing.value = true
  categoryPreview.value = ''
  previewError.value = ''
  try {
    const result = await gdriveDirectoryApi.previewCategory(
      {
        ...form,
        source_path: form.source_path || '/preview',
        archive_path: form.archive_path || '/preview-target',
        category_policy: cleanPolicy(),
      },
      sample.filename,
      {
        ...sample,
        include_keywords: sample.include_keywords
          .split(',')
          .map(value => value.trim())
          .filter(Boolean),
        actors: sample.actors
          .split(',')
          .map(value => value.trim())
          .filter(Boolean),
        directors: sample.directors
          .split(',')
          .map(value => value.trim())
          .filter(Boolean),
        genre_ids: sample.genre_ids
          .split(',')
          .filter(Boolean)
          .map(value => Number(value.trim())),
        origin_country: sample.origin_country
          .split(',')
          .filter(Boolean)
          .map(value => value.trim()),
        production_countries: sample.production_countries
          .split(',')
          .filter(Boolean)
          .map(value => ({ iso_3166_1: value.trim() })),
      },
      controller.signal,
    )
    if (disposed) return
    if (result.success && result.data) {
      categoryPreview.value = result.data.path
      frozenPolicy.value = result.data.policy
    } else previewError.value = result.message || t('gdrive.directory.operationFailed')
  } catch {
    if (!disposed) previewError.value = t('gdrive.directory.operationFailed')
  } finally {
    if (!disposed) previewing.value = false
  }
}
watch(
  [() => form.classification_mode, () => form.rule_id, policy, sample],
  () => {
    categoryPreview.value = ''
    frozenPolicy.value = undefined
    previewError.value = ''
  },
  { deep: true },
)
const section = ref('paths')
const directUpload = ref(!!form.upload_target)
watch(directUpload, value => {
  form.upload_target = value ? { account_ids: [], drive_id: '', folder_id: '', path: '', strm_prefix: '' } : null
  if (value && !form.archive_path) form.archive_path = '/'
})
const rules = ref<GdriveRule[]>([])
const ruleEditor = ref(false)
const ruleError = ref(false)
const controller = new AbortController()
const ruleOptions = computed(() => [
  { title: t('gdrive.studio.keepStructure'), value: null },
  ...rules.value.map(rule => ({ title: rule.name, value: rule.id })),
])
const selectedRule = computed(() => rules.value.find(rule => rule.id === form.rule_id))
async function loadRules() {
  try {
    rules.value = await gdriveRuleApi.list(controller.signal)
    ruleError.value = false
  } catch {
    if (!disposed) ruleError.value = true
  }
}
function ruleSaved() {
  ruleEditor.value = false
  void loadRules()
}
const extensions = ref(form.extensions.join(', '))
const keywords = ref(form.ignore_keywords.join(', '))
const suffixes = ref(form.ignore_suffixes.join(', '))
const saving = ref(false)
const uncertain = ref(false)
const error = ref('')
let disposed = false
const split = (value: string) =>
  value
    .split(/[,;\n，；]/)
    .map(item => item.trim())
    .filter(Boolean)
const valid = computed(
  () =>
    form.name.trim() &&
    form.source_path.trim().startsWith('/') &&
    form.archive_path.trim().startsWith('/') &&
    (!form.upload_target ||
      (form.upload_target.account_ids.length > 0 &&
        form.upload_target.drive_id.trim() &&
        form.upload_target.folder_id.trim())) &&
    split(extensions.value).length > 0 &&
    Number.isInteger(Number(form.interval_minutes)) &&
    Number(form.interval_minutes) >= 1 &&
    Number(form.interval_minutes) <= 43200 &&
    Number.isInteger(Number(form.min_size_mb)) &&
    Number(form.min_size_mb) >= 0 &&
    Number(form.min_size_mb) <= 1048576 &&
    Number.isInteger(Number(form.settle_seconds)) &&
    Number(form.settle_seconds) >= 0 &&
    Number(form.settle_seconds) <= 604800,
)
async function save() {
  if (!valid.value || saving.value || uncertain.value) return
  saving.value = true
  error.value = ''
  try {
    const response = await gdriveDirectoryApi.save(
      {
        ...form,
        category_policy: cleanPolicy(),
        name: form.name.trim(),
        source_path: form.source_path.trim(),
        archive_path: form.archive_path.trim(),
        extensions: split(extensions.value),
        ignore_keywords: split(keywords.value),
        ignore_suffixes: split(suffixes.value),
        min_size_mb: Number(form.min_size_mb),
        settle_seconds: Number(form.settle_seconds),
        interval_minutes: Number(form.interval_minutes),
      },
      props.task?.id,
    )
    if (disposed) return
    if (!response.success) error.value = response.message || t('gdrive.directory.operationFailed')
    else emit('saved')
  } catch {
    if (!disposed) {
      uncertain.value = true
      error.value = t('gdrive.directory.uncertain')
    }
  } finally {
    if (!disposed) saving.value = false
  }
}
onUnmounted(() => {
  disposed = true
  controller.abort()
})
onMounted(loadRules)
</script>

<template>
  <VDialog
    :model-value="true"
    :aria-label="task ? t('gdrive.directory.edit') : t('gdrive.directory.new')"
    max-width="1080"
    scrollable
    :persistent="saving"
    @update:model-value="!saving && emit('close')"
  >
    <VCard :title="task ? t('gdrive.directory.edit') : t('gdrive.directory.new')">
      <VCardText class="directory-dialog-body d-flex flex-column ga-4">
        <VAlert type="info" variant="tonal">{{ t('gdrive.directory.saveHint') }}</VAlert>
        <VTabs v-model="section" show-arrows>
          <VTab value="paths">1 · {{ t('gdrive.studio.paths') }}</VTab>
          <VTab value="rules">2 · {{ t('gdrive.studio.rulesFilters') }}</VTab>
          <VTab value="schedule">3 · {{ t('gdrive.studio.trigger') }}</VTab>
          <VTab value="review">4 · {{ t('gdrive.studio.review') }}</VTab>
        </VTabs>
        <fieldset :disabled="saving || uncertain" class="directory-fields">
          <div v-show="section === 'paths'" class="directory-fields">
            <VTextField
              v-model="form.name"
              :mobile-layout="false"
              :label="t('gdrive.directory.name')"
              maxlength="100"
            />
            <GdrivePathField
              v-model="form.source_path"
              :disabled="saving || uncertain"
              :label="t('gdrive.directory.source')"
            />
            <VSwitch v-model="directUpload" label="通过 Google Drive API 直接上传" color="primary" />
            <GdriveUploadTarget v-if="directUpload && form.upload_target" v-model="form.upload_target" />
            <GdrivePathField
              v-if="!directUpload"
              v-model="form.archive_path"
              :disabled="saving || uncertain"
              :label="t('gdrive.directory.target')"
              :hint="t('gdrive.directory.pathHint')"
            />
            <VSwitch v-model="form.recursive" :label="t('gdrive.directory.recursive')" color="primary" hide-details />
            <GdrivePathField
              v-model="form.strm_root!"
              :disabled="saving || uncertain"
              :label="t('gdrive.classification.strmRoot')"
              :hint="t('gdrive.classification.strmHint')"
            />
          </div>
          <div v-show="section === 'rules'" class="directory-fields">
            <VSelect
              v-model="form.classification_mode"
              :items="modeOptions"
              :label="t('gdrive.classification.mode')"
              :mobile-layout="false"
            />
            <VAlert type="info" variant="tonal">{{ t('gdrive.classification.offlineHint') }}</VAlert>
            <template v-if="form.classification_mode !== 'inherit'">
              <GdriveCategoryEditor v-if="form.classification_mode === 'custom'" v-model="policy" />
              <VAlert v-else type="info" variant="tonal">{{ t('gdrive.classification.moviepilotHint') }}</VAlert>
              <VSelect
                v-model="policy.unmatched"
                :items="unmatchedOptions"
                :label="t('gdrive.classification.unmatched')"
                :mobile-layout="false"
              />
              <VTextField
                v-if="policy.unmatched === 'fallback'"
                v-model="policy.fallback"
                :label="t('gdrive.classification.fallbackName')"
                :mobile-layout="false"
              />
            </template>
            <div class="d-flex align-start flex-wrap ga-3">
              <VSelect
                v-model="form.rule_id"
                :items="ruleOptions"
                :label="t('gdrive.studio.archiveRule')"
                class="flex-grow-1"
              />
              <VBtn variant="tonal" prepend-icon="mdi-plus" @click="ruleEditor = true">{{
                t('gdrive.studio.newRule')
              }}</VBtn>
            </div>
            <VAlert v-if="ruleError" type="error" variant="tonal"
              >{{ t('gdrive.studio.ruleLoadError')
              }}<VBtn variant="text" @click="loadRules">{{ t('gdrive.retry') }}</VBtn></VAlert
            >
            <VAlert type="info" variant="tonal">{{
              form.rule_id ? t('gdrive.studio.localMetadata') : t('gdrive.studio.keepStructureHint')
            }}</VAlert>
            <div v-if="selectedRule" class="rule-summary pa-4">
              <p class="text-subtitle-2">{{ selectedRule.name }} · v{{ selectedRule.revision }}</p>
              <code>{{ selectedRule.movie_directory }}/{{ selectedRule.movie_filename }}</code>
              <code>{{ selectedRule.tv_directory }}/{{ selectedRule.tv_filename }}</code>
              <p v-if="selectedRule.category_policy">
                {{
                  t('gdrive.classification.ruleCount', {
                    movie: selectedRule.category_policy.movie.length,
                    tv: selectedRule.category_policy.tv.length,
                  })
                }}
              </p>
              <p v-if="selectedRule.category_policy && form.classification_mode === 'inherit'">
                {{ t('gdrive.classification.ruleActive') }}
              </p>
            </div>
            <VExpansionPanels variant="accordion">
              <VExpansionPanel :title="t('gdrive.classification.preview')">
                <VExpansionPanelText>
                  <div class="directory-fields">
                    <VTextField
                      v-model="sample.filename"
                      :label="t('gdrive.classification.sampleFile')"
                      :mobile-layout="false"
                    />
                    <VSelect
                      v-model="sample.media_type"
                      :items="[
                        { title: t('mediaType.movie'), value: 'movie' },
                        { title: t('mediaType.tv'), value: 'tv' },
                      ]"
                      :label="t('common.media')"
                    />
                    <VTextField
                      v-for="field in [
                        'original_language',
                        'origin_country',
                        'production_countries',
                        'genre_ids',
                        'year',
                        'title',
                        'include_keywords',
                        'actors',
                        'directors',
                      ] as const"
                      :key="field"
                      v-model="sample[field]"
                      :label="t(`gdrive.classification.${field === 'year' ? 'release_year' : field}`)"
                      :mobile-layout="false"
                    />
                    <VBtn :loading="previewing" variant="tonal" @click="previewCategory">{{
                      t('gdrive.classification.preview')
                    }}</VBtn>
                    <code v-if="categoryPreview">{{ categoryPreview }}</code>
                    <VAlert v-if="previewError" type="error" variant="tonal">{{ previewError }}</VAlert>
                    <GdriveCategoryEditor
                      v-if="form.classification_mode === 'moviepilot' && frozenPolicy"
                      v-model="frozenPolicy"
                      readonly
                    />
                  </div>
                </VExpansionPanelText>
              </VExpansionPanel>
            </VExpansionPanels>
            <VTextField
              v-model="extensions"
              :mobile-layout="false"
              :label="t('gdrive.directory.extensions')"
              :hint="t('gdrive.directory.filterHint')"
              persistent-hint
            />
            <VTextField v-model="keywords" :mobile-layout="false" :label="t('gdrive.directory.keywords')" />
            <VTextField v-model="suffixes" :mobile-layout="false" :label="t('gdrive.directory.suffixes')" />
            <VTextField
              v-model="form.min_size_mb"
              :mobile-layout="false"
              type="number"
              min="0"
              :label="t('gdrive.directory.minSize')"
            />
            <VTextField
              v-model="form.settle_seconds"
              :mobile-layout="false"
              type="number"
              min="0"
              :label="t('gdrive.directory.settle')"
              :hint="t('gdrive.directory.settleHint')"
              persistent-hint
            />
          </div>
          <div v-show="section === 'schedule'" class="directory-fields">
            <VSwitch v-model="form.enabled" :label="t('gdrive.directory.enabled')" color="primary" hide-details />
            <VSwitch
              v-model="form.transfer_enabled"
              :label="t('gdrive.classification.transfer')"
              color="primary"
              hide-details
            />
            <p class="text-caption">{{ t('gdrive.classification.transferHint') }}</p>
            <VSwitch
              v-model="form.schedule_enabled"
              :label="t('gdrive.directory.schedule')"
              color="primary"
              hide-details
            />
            <VTextField
              v-model="form.interval_minutes"
              :mobile-layout="false"
              type="number"
              min="1"
              max="43200"
              :label="t('gdrive.directory.interval')"
              :hint="t('gdrive.directory.timeHint')"
              persistent-hint
            />
            <VSwitch v-model="form.remove_source" :label="t('gdrive.removeSource')" color="warning" hide-details />
            <VAlert :type="form.remove_source ? 'warning' : 'info'" variant="tonal">{{
              t(form.remove_source ? 'gdrive.willRemove' : 'gdrive.willKeep')
            }}</VAlert>
          </div>
          <div v-show="section === 'review'" class="directory-fields">
            <h3 class="text-h6">{{ form.name || t('gdrive.directory.name') }}</h3>
            <div class="review-path pa-4">
              <p class="text-caption">{{ t('gdrive.directory.source') }}</p>
              <code>{{ form.source_path || '—' }}</code>
              <p class="text-caption mt-3">{{ t('gdrive.directory.target') }}</p>
              <code>{{ form.archive_path || '—' }}</code>
            </div>
            <p>{{ t('gdrive.studio.archiveRule') }}: {{ selectedRule?.name || t('gdrive.studio.keepStructure') }}</p>
            <p>{{ t('gdrive.classification.mode') }}: {{ t(`gdrive.classification.${form.classification_mode}`) }}</p>
            <p>
              {{ t('gdrive.classification.strmRoot') }}: {{ form.strm_root || t('gdrive.classification.sourceSide') }}
            </p>
            <p>
              {{ t('gdrive.classification.transfer') }}:
              {{ form.transfer_enabled ? t('gdrive.directory.enabled') : t('gdrive.directory.disabled') }}
            </p>
            <p v-if="form.classification_mode !== 'inherit'">
              {{ t('gdrive.classification.unmatched') }}: {{ t(`gdrive.classification.${policy.unmatched}`) }}
              {{ policy.unmatched === 'fallback' ? policy.fallback : '' }}
            </p>
            <p>
              {{ t(form.schedule_enabled ? 'gdrive.directory.schedule' : 'gdrive.directory.manual')
              }}<span v-if="form.schedule_enabled"> · {{ form.interval_minutes }} min</span> ·
              {{ t(form.remove_source ? 'gdrive.removeSource' : 'gdrive.keepSource') }}
            </p>
            <GdriveFlow preview />
            <p class="text-body-2 text-medium-emphasis">{{ t('gdrive.studio.saveThenPreview') }}</p>
          </div>
        </fieldset>
        <VAlert v-if="error" type="error" role="alert" variant="tonal">{{ error }}</VAlert>
      </VCardText>
      <VCardActions class="pa-4">
        <VBtn :disabled="saving" @click="emit('close')">{{ t('common.cancel') }}</VBtn>
        <VSpacer />
        <VBtn
          v-if="section !== 'review'"
          variant="tonal"
          @click="section = section === 'paths' ? 'rules' : section === 'rules' ? 'schedule' : 'review'"
          >{{ t('gdrive.studio.next') }}</VBtn
        >
        <VBtn color="primary" variant="flat" :loading="saving" :disabled="!valid || uncertain" @click="save">{{
          t('gdrive.directory.save')
        }}</VBtn>
      </VCardActions>
    </VCard>
  </VDialog>
  <GdriveRuleDialog v-if="ruleEditor" @close="ruleEditor = false" @saved="ruleSaved" />
</template>

<style scoped>
.directory-dialog-body > * {
  flex-shrink: 0;
}
.directory-fields {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  min-inline-size: 0;
  border: 0;
  padding: 0;
}
.rule-summary,
.review-path {
  background: rgba(var(--v-theme-primary), 0.05);
  border-radius: 0.6rem;
  overflow-wrap: anywhere;
}
.rule-summary code {
  display: block;
  margin-block-start: 0.5rem;
}
</style>
