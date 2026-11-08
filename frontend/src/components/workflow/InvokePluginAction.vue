<script setup lang="ts">
import api from '@/api'
import { Handle, Position } from '@vue-flow/core'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const props = defineProps({
  id: {
    type: String,
    required: true,
  },
  data: {
    type: Object,
    required: true,
  },
})

interface ActionItem {
  id: string
  name: string
}

interface PluginAction {
  plugin_id: string
  plugin_name: string
  actions: ActionItem[]
}

// 插件所有动作
const pluginActions = ref<PluginAction[]>([])

// 插件选项
const pluginOptions = computed(() => {
  return pluginActions.value.map((item: PluginAction) => ({
    title: item.plugin_name,
    value: item.plugin_id,
  }))
})

// 动作选项
const actionOptions = computed(() => {
  return pluginActions.value
    .find((item: PluginAction) => item.plugin_id === props.data.plugin_id)
    ?.actions.map((item: ActionItem) => ({
      title: item.name,
      value: item.id,
    }))
})

// 用于在文本框显示和保存时转换action_params
const actionParamsText = computed({
  get: () => {
    try {
      return typeof props.data.action_params === 'object'
        ? JSON.stringify(props.data.action_params, null, 2)
        : props.data.action_params || ''
    } catch (error) {
      console.error(error)
      return ''
    }
  },
  set: (value: string) => {
    try {
      props.data.action_params = value ? JSON.parse(value) : {}
    } catch (error) {
      // 如果JSON解析失败，保留原始文本
      props.data.action_params = value
      console.error(error)
    }
  },
})

// 加载动作选项
async function loadPluginActions() {
  try {
    pluginActions.value = await api.get('workflow/plugin/actions')
  } catch (error) {
    console.error(error)
  }
}

onMounted(() => {
  loadPluginActions()
})
</script>
<template>
  <div>
    <VCard max-width="20rem">
      <Handle id="edge_in" type="target" :position="Position.Left" />
      <VCardItem>
        <template v-slot:prepend>
          <VAvatar>
            <VIcon icon="mdi-run" size="x-large"></VIcon>
          </VAvatar>
        </template>
        <VCardTitle>{{ t('workflow.invokePlugin.title') }}</VCardTitle>
        <VCardSubtitle>{{ t('workflow.invokePlugin.subtitle') }}</VCardSubtitle>
      </VCardItem>
      <VDivider />
      <VCardText>
        <VRow>
          <VCol cols="12">
            <VSelect
              v-model="data.plugin_id"
              :items="pluginOptions"
              :label="t('workflow.invokePlugin.plugin')"
              outlined
              dense
            />
          </VCol>
          <VCol cols="12">
            <VSelect
              v-model="data.action_id"
              :items="actionOptions"
              :label="t('workflow.invokePlugin.actionid')"
              outlined
              dense
            />
          </VCol>
          <VCol cols="12">
            <VTextarea v-model="actionParamsText" :label="t('workflow.invokePlugin.actionParams')" outlined dense />
          </VCol>
        </VRow>
      </VCardText>
      <Handle id="edge_out" type="source" :position="Position.Right" />
    </VCard>
  </div>
</template>
