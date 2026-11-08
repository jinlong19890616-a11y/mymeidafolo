<script setup lang="ts">
import MediaCardListView from '@/views/discover/MediaCardListView.vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

// 过滤参数
const filterParams = reactive({
  'type': 2,
  'cat': null,
  'sort': 'rank', // date/rank
  'year': null,
})

// Bangumi cat字典
/**
 * 0 为 其他
1 为 TV
2 为 OVA
3 为 Movie
5 为 WEB
 */
const bangumiCatDict = {
  '0': t('bangumi.cat.other'),
  '1': t('bangumi.cat.tv'),
  '2': t('bangumi.cat.ova'),
  '3': t('bangumi.cat.movie'),
  '5': t('bangumi.cat.web'),
}

// Bangumi排序字典
const bangumiSortDict = {
  'rank': t('bangumi.sortType.rank'),
  'date': t('bangumi.sortType.date'),
}

// 年份字典，自动生成最近10年
const yearDict: Record<number, number> = {}
const currentYear = new Date().getFullYear()
for (let i = 0; i < 10; i++) {
  yearDict[currentYear - i] = currentYear - i
}

// 当前Key
const currentKey = ref(0)

// 类型和过滤参数变化后重新刷新列表
watch([filterParams], () => {
  currentKey.value++
})
</script>

<template>
  <div class="px-3">
    <div class="flex justify-start align-center">
      <div class="mr-5">
        <VLabel>{{ t('bangumi.category') }}</VLabel>
      </div>
      <VChipGroup v-model="filterParams.cat">
        <VChip
          :color="filterParams.cat == key ? 'primary' : ''"
          filter
          tile
          :value="key"
          v-for="(value, key) in bangumiCatDict"
          :key="key"
        >
          {{ value }}
        </VChip>
      </VChipGroup>
    </div>
    <div class="flex justify-start align-center">
      <div class="mr-5">
        <VLabel>{{ t('bangumi.sort') }}</VLabel>
      </div>
      <VChipGroup v-model="filterParams.sort">
        <VChip
          :color="filterParams.sort == key ? 'primary' : ''"
          filter
          tile
          :value="key"
          v-for="(value, key) in bangumiSortDict"
          :key="key"
        >
          {{ value }}
        </VChip>
      </VChipGroup>
    </div>
    <div class="flex justify-start align-center">
      <div class="mr-5">
        <VLabel>{{ t('bangumi.year') }}</VLabel>
      </div>
      <VChipGroup v-model="filterParams.year">
        <VChip
          :color="filterParams.year == key ? 'primary' : ''"
          filter
          tile
          :value="key"
          v-for="(value, key) in yearDict"
          :key="key"
        >
          {{ value }}
        </VChip>
      </VChipGroup>
    </div>
  </div>
  <div>
    <MediaCardListView :key="currentKey" apipath="discover/bangumi" :params="filterParams" />
  </div>
</template>
