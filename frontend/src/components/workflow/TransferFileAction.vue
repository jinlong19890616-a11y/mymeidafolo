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

// 来源下拉框
const sourceOptions = ref([
  {
    title: t('workflow.transferFile.sourceOptions.fileList'),
    value: 'files',
  },
  {
    title: t('workflow.transferFile.sourceOptions.downloads'),
    value: 'downloads',
  },
])
</script>
<template>
  <div>
    <VCard max-width="20rem">
      <Handle id="edge_in" type="target" :position="Position.Left" />
      <VCardItem>
        <template v-slot:prepend>
          <VAvatar>
            <VIcon icon="mdi-file-move" size="x-large"></VIcon>
          </VAvatar>
        </template>
        <VCardTitle>{{ t('workflow.transferFile.title') }}</VCardTitle>
        <VCardSubtitle>{{ t('workflow.transferFile.subtitle') }}</VCardSubtitle>
      </VCardItem>
      <VDivider />
      <VCardText>
        <VRow>
          <VCol cols="12">
            <VSelect
              v-model="data.source"
              :label="t('workflow.transferFile.source')"
              :items="sourceOptions"
              outlined
              dense
            />
          </VCol>
        </VRow>
      </VCardText>
      <Handle id="edge_out" type="source" :position="Position.Right" />
    </VCard>
  </div>
</template>
