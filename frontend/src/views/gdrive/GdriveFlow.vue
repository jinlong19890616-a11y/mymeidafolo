<script setup lang="ts">
import type { GdriveScan } from '@/api/gdrive'
import { useI18n } from 'vue-i18n'
const props = defineProps<{ scan?: GdriveScan; stage?: string | null; status?: string | null; preview?: boolean }>()
const { t } = useI18n()
const stages = ['scan', 'filter', 'naming', 'uploading', 'archiving', 'generating_strm', 'scraping_metadata', 'refreshing_library', 'completed']
const current = computed(() => {
  if (props.preview || props.scan?.dry_run) {
    if (props.scan?.status === 'scanning') return 0
    return props.scan?.status === 'failed' || props.scan?.status === 'interrupted' ? 0 : 2
  }
  if (props.status === 'completed') return stages.length - 1
  if (props.stage === 'pending') return 2
  if (props.stage) return stages.indexOf(props.stage)
  if (props.scan?.status === 'scanning') return 0
  if (props.scan?.status === 'completed') return stages.length - 1
  return -1
})
</script>

<template>
  <ol class="gdrive-flow" :aria-label="t('gdrive.studio.flow')">
    <li
      v-for="(stage, index) in stages"
      :key="stage"
      :class="{
        done: current > index || (index === stages.length - 1 && (status === 'completed' || scan?.status === 'completed')),
        active: current === index,
        halted:
          current === index &&
          (status === 'failed' ||
            status === 'cancelled' ||
            scan?.status === 'failed' ||
            scan?.status === 'interrupted'),
        muted: (preview || scan?.dry_run) && index > 2,
      }"
    >
      <span class="flow-number"
        ><VIcon v-if="current > index" icon="mdi-check" size="16" /><template v-else>{{ index + 1 }}</template></span
      >
      <span>{{ t(`gdrive.studio.stages.${stage}`) }}</span>
    </li>
  </ol>
  <p v-if="preview || scan?.dry_run" class="text-caption text-medium-emphasis mt-2">
    {{ t('gdrive.studio.previewStops') }}
  </p>
</template>

<style scoped>
.gdrive-flow {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(80px, 1fr));
  gap: 0.5rem;
  list-style: none;
  padding: 0;
  margin: 0;
}
.gdrive-flow li {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  font-size: 0.8rem;
  color: rgba(var(--v-theme-on-surface), 0.85);
  padding: 0.75rem 0.5rem;
  border-radius: 0.5rem;
  background: rgba(var(--v-theme-on-surface), 0.035);
}
.flow-number {
  display: grid;
  place-items: center;
  border-radius: 50%;
  inline-size: 1.7rem;
  block-size: 1.7rem;
  background: rgba(var(--v-theme-on-surface), 0.08);
}
.gdrive-flow .active {
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
}
.gdrive-flow .done {
  color: rgb(var(--v-theme-success));
}
.gdrive-flow .halted {
  color: rgb(var(--v-theme-error));
  background: rgba(var(--v-theme-error), 0.1);
}
.gdrive-flow .muted {
  color: rgba(var(--v-theme-on-surface), 0.7);
  background: rgba(var(--v-theme-on-surface), 0.02);
}
@media (max-width: 600px) {
  .gdrive-flow {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .gdrive-flow li {
    flex-direction: row;
    align-items: center;
  }
}
</style>
