/**
 * PWA加载状态管理器
 * 用于协调不同组件的加载状态，确保所有关键资源加载完成后再显示界面
 */
export class PWALoadingStateManager {
  private loadingStates: Map<string, boolean> = new Map()
  private listeners: Set<(isLoading: boolean) => void> = new Set()

  /**
   * 设置加载状态
   * @param key 状态键名
   * @param loading 是否正在加载
   */
  setLoadingState(key: string, loading: boolean): void {
    const wasLoading = this.isAnyLoading()
    this.loadingStates.set(key, loading)
    const isLoading = this.isAnyLoading()
    
    // 如果总体加载状态发生变化，通知监听器
    if (wasLoading !== isLoading) {
      this.notifyListeners(isLoading)
    }
  }

  /**
   * 检查是否有任何组件正在加载
   */
  isAnyLoading(): boolean {
    return Array.from(this.loadingStates.values()).some(loading => loading)
  }

  /**
   * 等待所有加载完成
   */
  waitForAllComplete(): Promise<void> {
    return new Promise((resolve) => {
      if (!this.isAnyLoading()) {
        resolve()
        return
      }

      const checkComplete = () => {
        if (!this.isAnyLoading()) {
          resolve()
        } else {
          // 检查间隔
          setTimeout(checkComplete, 50)
        }
      }
      checkComplete()
    })
  }

  /**
   * 添加状态变化监听器
   * @param listener 监听器函数
   */
  addListener(listener: (isLoading: boolean) => void): void {
    this.listeners.add(listener)
  }

  /**
   * 移除状态变化监听器
   * @param listener 监听器函数
   */
  removeListener(listener: (isLoading: boolean) => void): void {
    this.listeners.delete(listener)
  }

  /**
   * 通知所有监听器
   * @param isLoading 是否正在加载
   */
  private notifyListeners(isLoading: boolean): void {
    this.listeners.forEach(listener => {
      try {
        listener(isLoading)
      } catch (error) {
        // 静默处理错误
      }
    })
  }

  /**
   * 获取当前加载状态详情
   */
  getLoadingStates(): Record<string, boolean> {
    return Object.fromEntries(this.loadingStates)
  }

  /**
   * 重置所有加载状态
   */
  reset(): void {
    const wasLoading = this.isAnyLoading()
    this.loadingStates.clear()
    
    if (wasLoading) {
      this.notifyListeners(false)
    }
  }
}

// 全局实例
export const globalLoadingStateManager = new PWALoadingStateManager()