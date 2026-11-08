// 预定义的颜色数组，包含更多丰富的颜色选项
const COLORS = [
  // 基础颜色
  '#4caf50', // 绿色
  '#2196f3', // 蓝色
  '#ff9800', // 橙色
  '#9c27b0', // 紫色
  '#f44336', // 红色
  '#00bcd4', // 青色
  '#8bc34a', // 浅绿色
  '#ff5722', // 深橙色
  '#3f51b5', // 靛蓝色
  '#009688', // 青绿色
  '#e91e63', // 粉红色
  '#673ab7', // 深紫色
  '#ffc107', // 琥珀色
  '#795548', // 棕色
  '#607d8b', // 蓝灰色

  // 扩展颜色
  '#ff4081', // 深粉红色
  '#00e676', // 浅绿色
  '#ff6f00', // 深橙色
  '#4fc3f7', // 浅蓝色
  '#ba68c8', // 浅紫色
  '#81c784', // 浅绿色
  '#ffb74d', // 浅橙色
  '#64b5f6', // 浅蓝色
  '#f06292', // 浅粉红色
  '#4db6ac', // 浅青绿色
  '#aed581', // 浅绿色
  '#ffd54f', // 浅黄色
  '#7986cb', // 浅靛蓝色
  '#4dd0e1', // 浅青色
  '#ff8a65', // 浅红色
  '#9575cd', // 浅紫色
  '#4fc3f7', // 天蓝色
  '#ffcc02', // 金黄色
  '#7cb342', // 浅绿色
  '#42a5f5', // 蓝色
  '#ab47bc', // 紫色
  '#26a69a', // 青绿色
  '#66bb6a', // 绿色
  '#ff7043', // 深橙色
  '#29b6f6', // 浅蓝色
  '#7e57c2', // 紫色
  '#26c6da', // 青色
  '#9ccc65', // 浅绿色
  '#ffb300', // 琥珀色
  '#8d6e63', // 棕色
  '#78909c', // 蓝灰色
  '#ef5350', // 红色
  '#ec407a', // 粉红色
  '#ab47bc', // 紫色
  '#42a5f5', // 蓝色
  '#7cb342', // 绿色
  '#ffa726', // 橙色
  '#26c6da', // 青色
  '#d4e157', // 浅绿色
  '#ffca28', // 黄色
  '#9fa8da', // 浅靛蓝色
  '#80cbc4', // 浅青绿色
  '#c5e1a5', // 浅绿色
  '#ffe082', // 浅黄色
  '#b39ddb', // 浅紫色
  '#90caf9', // 浅蓝色
  '#a5d6a7', // 浅绿色
  '#ffcc80', // 浅橙色
  '#b2dfdb', // 浅青绿色
  '#f8bbd9', // 浅粉红色
  '#c8e6c9', // 浅绿色
  '#fff9c4', // 浅黄色
  '#d1c4e9', // 浅紫色
  '#bbdefb', // 浅蓝色
  '#c8e6c9', // 浅绿色
  '#ffecb3', // 浅琥珀色
  '#d7ccc8', // 浅棕色
  '#cfd8dc', // 浅蓝灰色
]

// 颜色缓存，确保同一项目总是获得相同颜色
const colorCache = new Map<string, string>()

/**
 * 生成随机颜色
 * @returns 随机颜色值
 */
export function generateRandomColor(): string {
  return COLORS[Math.floor(Math.random() * COLORS.length)]
}

/**
 * 为指定项目获取或生成颜色
 * @param itemKey 项目的唯一标识
 * @returns 颜色值
 */
export function getItemColor(itemKey: string): string {
  if (!colorCache.has(itemKey)) {
    colorCache.set(itemKey, generateRandomColor())
  }
  return colorCache.get(itemKey)!
}

/**
 * 初始化项目颜色
 * @param items 项目数组
 * @param keyExtractor 从项目中提取唯一键的函数
 */
export function initializeItemColors<T>(items: T[], keyExtractor: (item: T) => string): void {
  items.forEach(item => {
    const key = keyExtractor(item)
    getItemColor(key) // 这会自动缓存颜色
  })
}

/**
 * 清除颜色缓存
 */
export function clearColorCache(): void {
  colorCache.clear()
}

/**
 * 获取所有预定义颜色
 * @returns 颜色数组
 */
export function getAllColors(): string[] {
  return [...COLORS]
}

/**
 * 获取颜色总数
 * @returns 颜色数量
 */
export function getColorCount(): number {
  return COLORS.length
}
