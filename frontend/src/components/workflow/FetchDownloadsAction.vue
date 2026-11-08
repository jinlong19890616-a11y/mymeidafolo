<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
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
    <VCard max-width="20rem">
      <Handle id="edge_in" type="target" :position="Position.Left" />
      <VCardItem>
        <template v-slot:prepend>
          <VAvatar>
            <VIcon icon="mdi-progress-download" size="x-large"></VIcon>
          </VAvatar>
        </template>
        <VCardTitle>{{ t('workflow.fetchDownloads.title') }}</VCardTitle>
        <VCardSubtitle>{{ t('workflow.fetchDownloads.subtitle') }}</VCardSubtitle>
      </VCardItem>
      <VDivider />
      <VCardText>
        <VRow>
          <VCol cols="12">
            <VSwitch v-model="data.loop" :label="t('workflow.fetchDownloads.loop')" />
          </VCol>
          <VCol cols="12">
            <VTextField
              v-model="data.loop_interval"
              :disabled="!data.loop"
              type="number"
              :label="t('workflow.fetchDownloads.loopInterval')"
              outlined
              dense
              clearable
            />
          </VCol>
        </VRow>
      </VCardText>
      <Handle id="edge_out" type="source" :position="Position.Right" />
    </VCard>
  </div>
</template>
