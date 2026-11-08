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
            <VIcon icon="mdi-rss" size="x-large"></VIcon>
          </VAvatar>
        </template>
        <VCardTitle>{{ t('workflow.fetchRss.title') }}</VCardTitle>
        <VCardSubtitle>{{ t('workflow.fetchRss.subtitle') }}</VCardSubtitle>
      </VCardItem>
      <VDivider />
      <VCardText>
        <VRow>
          <VCol cols="12">
            <VTextField v-model="data.url" :label="t('workflow.fetchRss.url')" outlined dense clearable />
          </VCol>
          <VCol cols="12">
            <VTextField v-model="data.ua" :label="t('workflow.fetchRss.userAgent')" outlined dense clearable />
          </VCol>
          <VCol cols="12">
            <VTextField
              v-model="data.timeout"
              type="number"
              :label="t('workflow.fetchRss.timeout')"
              outlined
              dense
              clearable
            />
          </VCol>
          <VCol cols="6">
            <VSwitch v-model="data.match_media" :label="t('workflow.fetchRss.matchMedia')" />
          </VCol>
          <VCol cols="6">
            <VSwitch v-model="data.proxy" :label="t('workflow.fetchRss.useProxy')" />
          </VCol>
        </VRow>
      </VCardText>
      <Handle id="edge_out" type="source" :position="Position.Right" />
    </VCard>
  </div>
</template>
