<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

defineProps({
  id: {
    type: String,
    required: true,
  },
  data: {
    type: Object,
    required: true,
  },
})
</script>
<template>
  <div>
    <VCard max-width="20rem" class="note-card">
      <VCardItem class="py-2">
        <template v-slot:prepend>
          <VAvatar color="warning">
            <VIcon icon="mdi-note-text" size="x-large"></VIcon>
          </VAvatar>
        </template>
        <VCardTitle>{{ t('workflow.note.title') }}</VCardTitle>
      </VCardItem>
      <VDivider />
      <VCardText>
        <VRow>
          <VCol cols="12">
            <VTextarea
              v-model="data.content"
              :label="t('workflow.note.content')"
              :placeholder="t('workflow.note.placeholder')"
              outlined
              dense
              auto-grow
              rows="3"
              max-rows="6"
              clearable
            />
          </VCol>
        </VRow>
      </VCardText>
    </VCard>
  </div>
</template>

<style lang="scss" scoped>
.note-card {
  background: linear-gradient(135deg, rgba(var(--v-theme-warning), 0.1) 0%, rgba(var(--v-theme-warning), 0.05) 100%);

  &:hover {
    border-color: rgba(var(--v-theme-warning), 0.4);
  }
}
</style>
