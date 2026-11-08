<script lang="ts" setup>
// 输入参数
const props: any = inject('rankingPropsKey')
</script>

<template>
  <div class="title-wrapper">
    <div class="title-section">
      <div class="title-badge"></div>
      <h3 class="title-text">{{ props?.title }}</h3>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.title-wrapper {
  display: flex;
  align-items: center;
  justify-content: space-between;
  inline-size: 100%;
}

.title-section {
  position: relative;
  display: flex;
  align-items: center;
}

.title-badge {
  border-radius: 2px;
  background-color: rgb(var(--v-theme-primary));
  block-size: 16px;
  inline-size: 3px;
  margin-inline-end: 8px;
}

.title-text {
  padding: 0;
  margin: 0;
  color: rgba(var(--v-theme-on-background), 0.95);
  font-size: 1.2rem;
  font-weight: 600;
}
</style>
