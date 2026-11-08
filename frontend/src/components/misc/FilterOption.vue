<script lang="ts" setup>
defineProps<{ title: string }>()
</script>
<template>
  <VListSubheader>{{ title }}</VListSubheader>
  <VListItem><slot /></VListItem>
</template>
