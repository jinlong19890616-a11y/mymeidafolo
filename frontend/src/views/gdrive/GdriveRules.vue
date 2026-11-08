<script setup lang="ts">
import { gdriveRuleApi, type GdriveRule } from '@/api/gdrive'
import { useUserStore } from '@/stores'
import GdriveRuleDialog from './GdriveRuleDialog.vue'
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
const user = useUserStore()
const rules = ref<GdriveRule[]>([])
const editing = ref<GdriveRule>()
const open = ref(false)
const classify = ref(false)
const loading = ref(false)
const error = ref('')
const deleting = ref<GdriveRule>()
const busy = ref(false)
const controller = new AbortController()
async function refresh() {
  loading.value = true
  error.value = ''
  try {
    rules.value = await gdriveRuleApi.list(controller.signal)
  } catch {
    if (!controller.signal.aborted) error.value = t('gdrive.studio.ruleLoadError')
  } finally {
    if (!controller.signal.aborted) loading.value = false
  }
}
function edit(rule?: GdriveRule, classification = false) {
  editing.value = rule
  classify.value = classification
  open.value = true
}
function saved() {
  open.value = false
  void refresh()
}
async function remove() {
  if (!deleting.value || busy.value) return
  busy.value = true
  try {
    const result = await gdriveRuleApi.remove(deleting.value.id)
    if (!result.success) {
      error.value = result.message || t('gdrive.directory.operationFailed')
      return
    }
    void refresh()
  } catch {
    error.value = t('gdrive.directory.uncertain')
  } finally {
    busy.value = false
    deleting.value = undefined
  }
}
onMounted(refresh)
onUnmounted(() => controller.abort())
watch(
  () => user.superUser,
  value => {
    if (!value) {
      open.value = false
      deleting.value = undefined
    }
  },
)
</script>

<template>
  <div>
    <div class="d-flex flex-wrap align-center ga-3 mb-5">
      <div class="flex-grow-1">
        <h2 class="text-h6">{{ t('gdrive.studio.rules') }}</h2>
        <p class="text-body-2 text-medium-emphasis mt-1">{{ t('gdrive.studio.rulesHint') }}</p>
      </div>
      <VBtn :loading="loading" variant="tonal" @click="refresh">{{ t('gdrive.refresh') }}</VBtn>
      <VBtn v-if="user.superUser" color="primary" prepend-icon="mdi-plus" @click="edit()">{{
        t('gdrive.studio.newRule')
      }}</VBtn>
      <VBtn
        v-if="user.superUser"
        color="primary"
        variant="tonal"
        prepend-icon="mdi-file-tree-outline"
        @click="edit(undefined, true)"
        >{{ t('gdrive.classification.newRule') }}</VBtn
      >
    </div>
    <VAlert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</VAlert>
    <VAlert type="info" variant="tonal" class="mb-5">{{ t('gdrive.studio.localMetadata') }}</VAlert>
    <VCard v-if="!loading && !rules.length" class="pa-8 text-center"
      ><VIcon icon="mdi-file-tree-outline" size="40" class="mb-3" />
      <p>{{ t('gdrive.studio.emptyRules') }}</p></VCard
    >
    <div class="rules-grid">
      <VCard v-for="rule in rules" :key="rule.id">
        <VCardText>
          <div class="d-flex align-center ga-3 mb-4">
            <VAvatar color="primary" variant="tonal"><VIcon icon="mdi-file-tree-outline" /></VAvatar>
            <div>
              <h3 class="text-h6">{{ rule.name }}</h3>
              <p class="text-caption text-medium-emphasis">
                v{{ rule.revision }} · {{ t(`gdrive.studio.${rule.media_type}`) }}
              </p>
            </div>
          </div>
          <p class="text-caption text-medium-emphasis">{{ t('gdrive.studio.movie') }}</p>
          <code class="rule-code">{{ rule.movie_directory }}/{{ rule.movie_filename }}</code>
          <p class="text-caption text-medium-emphasis mt-3">{{ t('gdrive.studio.tv') }}</p>
          <code class="rule-code">{{ rule.tv_directory }}/{{ rule.tv_filename }}</code>
          <div class="d-flex flex-wrap ga-1 mt-4">
            <VChip v-if="rule.category_policy" color="primary" size="small">{{
              t('gdrive.classification.ruleCount', {
                movie: rule.category_policy.movie.length,
                tv: rule.category_policy.tv.length,
              })
            }}</VChip>
            <VChip
              v-for="(category, index) in rule.categories"
              :key="category"
              size="small"
              v-show="!rule.category_policy"
              >{{ index + 1 }} · {{ t(`gdrive.studio.variables.${category}`) }}</VChip
            >
          </div>
          <p v-if="rule.category_policy" class="text-caption mt-2">
            {{ t(`gdrive.classification.path_${rule.category_policy.path_mode || 'media_type'}`) }}
          </p>
        </VCardText>
        <VCardActions v-if="user.superUser"
          ><VBtn @click="edit(rule)">{{ t('gdrive.studio.editRule') }}</VBtn
          ><VSpacer /><VBtn color="error" @click="deleting = rule">{{ t('common.delete') }}</VBtn></VCardActions
        >
      </VCard>
    </div>
    <GdriveRuleDialog v-if="open" :rule="editing" :classify="classify" @close="open = false" @saved="saved" />
    <VDialog :model-value="!!deleting" max-width="500" :persistent="busy" @update:model-value="deleting = undefined">
      <VCard :title="t('gdrive.studio.deleteRule')"
        ><VCardText
          >{{ deleting?.name }}
          <p class="mt-3">{{ t('gdrive.studio.deleteRuleHint') }}</p></VCardText
        ><VCardActions
          ><VBtn :disabled="busy" @click="deleting = undefined">{{ t('common.cancel') }}</VBtn
          ><VSpacer /><VBtn color="error" :loading="busy" @click="remove">{{
            t('gdrive.directory.confirm')
          }}</VBtn></VCardActions
        ></VCard
      >
    </VDialog>
  </div>
</template>

<style scoped>
.rules-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 25rem), 1fr));
  gap: 1rem;
}
.rule-code {
  display: block;
  overflow-wrap: anywhere;
  font-size: 0.8rem;
}
</style>
