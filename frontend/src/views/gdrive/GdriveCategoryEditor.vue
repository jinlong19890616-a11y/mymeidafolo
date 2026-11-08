<script setup lang="ts">
import { gdriveRuleApi, type GdriveCategoryPolicy } from '@/api/gdrive'
import { useI18n } from 'vue-i18n'

const policy = defineModel<GdriveCategoryPolicy>({ required: true })
const props = defineProps<{ readonly?: boolean }>()
const { t } = useI18n()
const mediaType = ref<'movie' | 'tv'>('movie')
const fields = ['original_language', 'origin_country', 'production_countries', 'genre_ids', 'release_year']
const extraFields = ['year', 'keywords', 'include_keywords', 'series_keywords', 'series_actors', 'series_directors']
const yaml = ref('')
const importing = ref(false)
const error = ref('')
const warnings = ref<string[]>([])
const controller = new AbortController()
onUnmounted(() => controller.abort())
function conditionFields(conditions: Record<string, string>) {
  return [...new Set([...fields, ...Object.keys(conditions).map(key => key.replace(/^\?/, ''))])]
}
function conditionKey(conditions: Record<string, string>, field: string) {
  return `?${field}` in conditions ? `?${field}` : field
}
function optional(conditions: Record<string, string>, field: string, enabled: boolean | null) {
  const key = conditionKey(conditions, field)
  const value = conditions[key] || ''
  delete conditions[key]
  conditions[enabled ? `?${field}` : field] = value
}
async function importYaml() {
  if (importing.value || props.readonly) return
  importing.value = true
  error.value = ''
  warnings.value = []
  try {
    const result = await gdriveRuleApi.importCategories(yaml.value, policy.value, controller.signal)
    if (controller.signal.aborted) return
    if (result.success && result.data) {
      policy.value = result.data.policy
      warnings.value = result.data.warnings
    } else error.value = result.message || t('gdrive.directory.operationFailed')
  } catch {
    if (!controller.signal.aborted) error.value = t('gdrive.directory.operationFailed')
  } finally {
    if (!controller.signal.aborted) importing.value = false
  }
}
async function loadFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  if (file.size > 262144) error.value = t('gdrive.classification.fileTooLarge')
  else yaml.value = await file.text()
  input.value = ''
}
function exportYaml() {
  yaml.value = (['movie', 'tv'] as const)
    .map(
      type =>
        `${type}:\n${
          policy.value[type].length
            ? policy.value[type]
                .map(
                  rule =>
                    `  ${JSON.stringify(rule.name)}:${
                      Object.values(rule.conditions).some(Boolean)
                        ? `\n${Object.entries(rule.conditions)
                            .filter(([, value]) => value.trim())
                            .map(([key, value]) => `    ${JSON.stringify(key)}: ${JSON.stringify(value)}`)
                            .join('\n')}`
                        : ' null'
                    }`,
                )
                .join('\n')
            : '  {}'
        }`,
    )
    .join('\n')
}
function move(index: number, offset: number) {
  const rules = policy.value[mediaType.value]
  const [rule] = rules.splice(index, 1)
  rules.splice(index + offset, 0, rule)
}
</script>

<template>
  <div class="d-flex flex-column ga-3">
    <details class="category-help">
      <summary>{{ t('gdrive.classification.conditionsHelp') }}</summary>
      <VAlert type="info" variant="tonal" class="mt-2">{{ t('gdrive.classification.conditionsHint') }}</VAlert>
    </details>
    <VSelect
      v-model="policy.path_mode"
      :items="['media_type', 'relative'].map(value => ({ value, title: t(`gdrive.classification.path_${value}`) }))"
      :label="t('gdrive.classification.pathMode')"
      :mobile-layout="false"
      :readonly="props.readonly"
      :hint="t('gdrive.classification.pathHint')"
      persistent-hint
    />
    <VExpansionPanels v-if="!props.readonly" variant="accordion">
      <VExpansionPanel :title="t('gdrive.classification.yamlTitle')">
        <VExpansionPanelText>
          <p class="text-body-2 mb-3">{{ t('gdrive.classification.yamlHint') }}</p>
          <label class="d-block mb-3"
            >{{ t('gdrive.classification.loadFile') }}
            <input type="file" accept=".yaml,.yml,.txt,text/plain" :disabled="importing" @change="loadFile" />
          </label>
          <VTextarea v-model="yaml" :label="t('gdrive.classification.yamlContent')" rows="10" :disabled="importing" />
          <div class="d-flex flex-wrap ga-2">
            <VBtn :disabled="!yaml.trim()" :loading="importing" variant="tonal" @click="importYaml">{{
              t('gdrive.classification.importYaml')
            }}</VBtn>
            <VBtn :disabled="importing" variant="text" @click="exportYaml">{{
              t('gdrive.classification.exportYaml')
            }}</VBtn>
          </div>
          <VAlert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</VAlert>
          <VAlert v-for="warning in warnings" :key="warning" type="warning" variant="tonal" class="mt-3">{{
            warning
          }}</VAlert>
        </VExpansionPanelText>
      </VExpansionPanel>
    </VExpansionPanels>
    <VTabs v-model="mediaType">
      <VTab value="movie">{{ t('mediaType.movie') }}</VTab>
      <VTab value="tv">{{ t('mediaType.tv') }}</VTab>
    </VTabs>
    <details
      v-for="(rule, index) in policy[mediaType]"
      :key="`${mediaType}-${index}`"
      :open="index === 0"
      class="category-rule pa-3"
    >
      <summary class="category-summary">
        <span class="category-number">{{ index + 1 }}</span>
        <span class="category-title">{{ rule.name || t('gdrive.classification.categoryName') }}</span>
        <VIcon icon="mdi-chevron-down" size="20" class="category-chevron" />
      </summary>
      <div class="category-heading mt-4 mb-3">
        <VTextField
          v-model="rule.name"
          :label="t('gdrive.classification.categoryName')"
          :readonly="props.readonly"
          hide-details
          :mobile-layout="false"
        />
        <div v-if="!props.readonly" class="category-actions">
          <VBtn
            :disabled="index === 0"
            :aria-label="t('gdrive.classification.up')"
            icon="mdi-arrow-up"
            size="small"
            variant="text"
            @click="move(index, -1)"
          />
          <VBtn
            :disabled="index === policy[mediaType].length - 1"
            :aria-label="t('gdrive.classification.down')"
            icon="mdi-arrow-down"
            size="small"
            variant="text"
            @click="move(index, 1)"
          />
          <VBtn
            :aria-label="t('gdrive.classification.remove')"
            icon="mdi-close"
            size="small"
            variant="text"
            @click="policy[mediaType].splice(index, 1)"
          />
        </div>
      </div>
      <div class="condition-grid">
        <div v-for="field in conditionFields(rule.conditions)" :key="field">
          <VTextField
            v-model="rule.conditions[conditionKey(rule.conditions, field)]"
            :label="t(`gdrive.classification.${field === 'year' ? 'release_year' : field}`)"
            :readonly="props.readonly"
            :mobile-layout="false"
            hide-details
          />
          <VCheckbox
            v-if="!field.startsWith('series_')"
            :model-value="`?${field}` in rule.conditions"
            :label="t('gdrive.classification.optional')"
            :disabled="props.readonly"
            density="compact"
            hide-details
            @update:model-value="optional(rule.conditions, field, $event)"
          />
        </div>
      </div>
      <div v-if="!props.readonly" class="d-flex flex-wrap ga-1 mt-2">
        <VBtn
          v-for="field in extraFields.filter(field => !(field in rule.conditions) && !(`?${field}` in rule.conditions))"
          :key="field"
          size="small"
          variant="text"
          @click="rule.conditions[field] = ''"
          >+ {{ t(`gdrive.classification.${field === 'year' ? 'release_year' : field}`) }}</VBtn
        >
      </div>
      <p v-if="!Object.values(rule.conditions).some(Boolean)" class="text-caption mt-2">
        {{ t('gdrive.classification.catchAll') }}
      </p>
    </details>
    <VBtn
      v-if="!props.readonly"
      variant="tonal"
      prepend-icon="mdi-plus"
      :disabled="policy[mediaType].length >= 100"
      @click="policy[mediaType].push({ name: '', conditions: {} })"
      >{{ t('gdrive.classification.add') }}</VBtn
    >
  </div>
</template>

<style scoped>
.category-rule {
  background: rgba(var(--v-theme-primary), 0.04);
  border-radius: 0.6rem;
}
.category-help > summary {
  cursor: pointer;
  color: rgb(var(--v-theme-primary));
}
.category-summary {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  cursor: pointer;
  list-style: none;
}
.category-summary::-webkit-details-marker {
  display: none;
}
.category-summary:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 4px;
}
.category-number {
  color: rgb(var(--v-theme-primary));
  font-variant-numeric: tabular-nums;
}
.category-title {
  flex: 1;
  min-inline-size: 0;
  overflow-wrap: anywhere;
}
details[open] > summary .category-chevron {
  transform: rotate(180deg);
}
.category-heading {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.5rem;
}
.category-actions {
  display: flex;
  justify-content: flex-end;
}
.condition-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 14rem), 1fr));
  gap: 0.75rem;
}
@media (max-width: 600px) {
  .category-heading {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
