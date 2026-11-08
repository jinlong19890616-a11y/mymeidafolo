/**
 * PWA状态恢复插件 - 极简版
 * 只专注2个核心功能：路由、标签页
 */

import type { App } from 'vue'

// =============================================================================
// 1. 路由状态管理器
// =============================================================================

class RouteStateManager {
  private readonly STORAGE_KEY = 'pwa-current-route'

  // 保存当前路由
  saveCurrentRoute() {
    const route = {
      path: window.location.pathname,
      search: window.location.search,
      hash: window.location.hash,
      timestamp: Date.now(),
    }
    sessionStorage.setItem(this.STORAGE_KEY, JSON.stringify(route))
  }

  // 恢复路由
  restoreRoute() {
    try {
      const saved = sessionStorage.getItem(this.STORAGE_KEY)
      if (!saved) return null

      const route = JSON.parse(saved)
      // 检查是否过期（1小时）
      if (Date.now() - route.timestamp > 60 * 60 * 1000) {
        this.clearRoute()
        return null
      }

      return route
    } catch {
      return null
    }
  }

  // 清除路由状态
  clearRoute() {
    sessionStorage.removeItem(this.STORAGE_KEY)
  }

  // 初始化路由恢复
  init() {
    // 监听路由变化，自动保存
    window.addEventListener('popstate', () => this.saveCurrentRoute())

    // 页面隐藏时保存
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        this.saveCurrentRoute()
      }
    })

    // 页面卸载时保存
    window.addEventListener('beforeunload', () => {
      this.saveCurrentRoute()
    })
  }
}

// =============================================================================
// 2. 动态标签页状态管理器
// =============================================================================

class TabStateManager {
  private readonly STORAGE_KEY = 'pwa-active-tabs'

  // 保存标签页状态
  saveTabState(routePath: string, activeTab: string) {
    try {
      const allTabs = this.getAllTabStates()
      allTabs[routePath] = {
        activeTab,
        timestamp: Date.now(),
      }
      sessionStorage.setItem(this.STORAGE_KEY, JSON.stringify(allTabs))
    } catch (error) {
      console.warn('保存标签页状态失败:', error)
    }
  }

  // 获取标签页状态
  getTabState(routePath: string): string | null {
    try {
      const allTabs = this.getAllTabStates()
      const tabState = allTabs[routePath]

      if (!tabState) return null

      // 检查是否过期（1小时）
      if (Date.now() - tabState.timestamp > 60 * 60 * 1000) {
        delete allTabs[routePath]
        sessionStorage.setItem(this.STORAGE_KEY, JSON.stringify(allTabs))
        return null
      }

      return tabState.activeTab
    } catch {
      return null
    }
  }

  // 获取所有标签页状态
  private getAllTabStates(): Record<string, any> {
    try {
      const saved = sessionStorage.getItem(this.STORAGE_KEY)
      return saved ? JSON.parse(saved) : {}
    } catch {
      return {}
    }
  }

  // 清除标签页状态
  clearTabState(routePath?: string) {
    if (routePath) {
      const allTabs = this.getAllTabStates()
      delete allTabs[routePath]
      sessionStorage.setItem(this.STORAGE_KEY, JSON.stringify(allTabs))
    } else {
      sessionStorage.removeItem(this.STORAGE_KEY)
    }
  }
}

// =============================================================================
// 3. 主状态恢复管理器
// =============================================================================

class StateRestore {
  public route = new RouteStateManager()
  public tab = new TabStateManager()

  // 初始化
  init() {
    this.route.init()
    this.setupAutoRestore()
  }

  // 设置自动恢复
  private setupAutoRestore() {
    // 页面显示时检查是否需要恢复状态
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) {
        // 只恢复路由状态，不自动恢复标签页状态
        // 标签页状态由组件自己控制，避免干扰当前页面状态
        this.checkAndRestoreRoute()
      }
    })

    // 页面加载完成后恢复状态
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => {
        setTimeout(() => this.checkAndRestoreRoute(), 100)
      })
    } else {
      setTimeout(() => this.checkAndRestoreRoute(), 100)
    }
  }

  // 检查并恢复路由状态（不恢复标签页状态）
  private checkAndRestoreRoute() {
    // 只恢复路由（如果当前路径与保存的不同）
    const savedRoute = this.route.restoreRoute()
    if (savedRoute && savedRoute.path !== window.location.pathname) {
      const fullPath = savedRoute.path + savedRoute.search + savedRoute.hash
      console.log('恢复路由:', fullPath)
      window.history.replaceState(null, '', fullPath)
    }

    // 发送恢复事件，但标签页恢复由组件自己决定
    window.dispatchEvent(
      new CustomEvent('pwa-state-restore', {
        detail: {
          route: savedRoute,
          tabs: this.tab,
        },
      }),
    )
  }

  // 清除所有状态
  clearAllStates() {
    this.route.clearRoute()
    this.tab.clearTabState()
  }
}

// =============================================================================
// 4. Vue插件安装
// =============================================================================

const stateRestore = new StateRestore()

export default {
  install(app: App) {
    // 注册全局属性
    app.config.globalProperties.$stateRestore = stateRestore

    // 提供注入
    app.provide('stateRestore', stateRestore)

    // 初始化
    stateRestore.init()

    console.log('PWA状态恢复插件已安装（路由 + 标签页）')
  },
}

// 导出管理器实例
export { stateRestore }

// 导出类型
export type { RouteStateManager, TabStateManager, StateRestore }
