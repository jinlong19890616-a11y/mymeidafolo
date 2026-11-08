/**
 * PWA状态恢复组合式API
 * 提供2个专门的hooks：路由、标签页
 */

import { ref, onMounted, onUnmounted, watch, inject } from 'vue'
import { useRoute } from 'vue-router'
import type { StateRestore } from '@/plugins/stateRestore'

// =============================================================================
// 1. 动态标签页状态恢复
// =============================================================================

/**
 * 动态标签页状态恢复Hook
 * 自动保存和恢复v-tabs的当前激活标签
 */
export function useTabStateRestore(defaultTab?: string) {
  const route = useRoute()
  const stateRestore = inject<StateRestore>('stateRestore')

  const activeTab = ref<string>(defaultTab || '')

  // 保存标签页状态
  const saveTabState = (tab: string) => {
    if (stateRestore && tab) {
      stateRestore.tab.saveTabState(route.path, tab)
    }
  }

  // 恢复标签页状态
  const restoreTabState = () => {
    if (stateRestore) {
      const savedTab = stateRestore.tab.getTabState(route.path)
      if (savedTab) {
        activeTab.value = savedTab
        console.log(`恢复标签页状态: ${route.path} -> ${savedTab}`)
        return true
      }
    }
    return false
  }

  // 监听activeTab变化，自动保存
  watch(activeTab, newTab => {
    if (newTab) {
      saveTabState(newTab)
    }
  })

  // 组件挂载时恢复状态（仅在首次加载时）
  onMounted(() => {
    // 先尝试恢复，如果没有保存的状态则使用默认值
    if (!restoreTabState() && defaultTab) {
      activeTab.value = defaultTab
    }
  })

  return {
    activeTab,
    saveTabState,
    restoreTabState,
  }
}

// =============================================================================
// 2. 路由状态恢复
// =============================================================================

/**
 * 路由状态恢复Hook
 * 获取路由恢复信息，主要用于调试和监控
 */
export function useRouteStateRestore() {
  const stateRestore = inject<StateRestore>('stateRestore')

  const lastRestoredRoute = ref<any>(null)

  // 获取上次保存的路由
  const getLastSavedRoute = () => {
    if (stateRestore) {
      return stateRestore.route.restoreRoute()
    }
    return null
  }

  // 手动保存当前路由
  const saveCurrentRoute = () => {
    if (stateRestore) {
      stateRestore.route.saveCurrentRoute()
    }
  }

  // 清除路由状态
  const clearRouteState = () => {
    if (stateRestore) {
      stateRestore.route.clearRoute()
    }
  }

  // 监听全局恢复事件
  const handleRestore = (event: Event) => {
    const customEvent = event as CustomEvent
    if (customEvent.detail?.route) {
      lastRestoredRoute.value = customEvent.detail.route
    }
  }

  onMounted(() => {
    window.addEventListener('pwa-state-restore', handleRestore)
  })

  onUnmounted(() => {
    window.removeEventListener('pwa-state-restore', handleRestore)
  })

  return {
    lastRestoredRoute,
    getLastSavedRoute,
    saveCurrentRoute,
    clearRouteState,
  }
}

// =============================================================================
// 3. 全量状态恢复Hook
// =============================================================================

/**
 * 全量状态恢复Hook
 * 用于清理所有状态或获取统计信息
 */
export function useStateRestore() {
  const stateRestore = inject<StateRestore>('stateRestore')

  // 清除所有状态
  const clearAllStates = () => {
    if (stateRestore) {
      stateRestore.clearAllStates()
      console.log('已清除所有PWA状态')
    }
  }

  // 获取状态统计
  const getStateStats = () => {
    if (!stateRestore) return null

    return {
      hasRoute: !!stateRestore.route.restoreRoute(),
      // 可以扩展更多统计信息
    }
  }

  return {
    clearAllStates,
    getStateStats,
    stateRestore,
  }
}

// =============================================================================
// 4. 快捷Hook组合
// =============================================================================

/**
 * 页面级状态恢复Hook
 * 组合路由和标签页状态恢复功能，适用于有标签页的页面
 */
export function usePageStateRestore(defaultTab?: string) {
  const tabs = defaultTab ? useTabStateRestore(defaultTab) : null
  const route = useRouteStateRestore()
  const global = useStateRestore()

  return {
    tabs,
    route,
    global,
  }
}
