interface CacheInfo {
  cacheSizes: Record<string, number>
  totalSize: number
  totalSizeMB: string
}

export function useCacheManager() {
  const cacheInfo = ref<CacheInfo | null>(null)
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  // 发送消息到Service Worker
  async function sendMessageToSW(message: any): Promise<any> {
    if (!('serviceWorker' in navigator)) {
      throw new Error('Service Worker not supported')
    }

    const registration = await navigator.serviceWorker.ready
    const messageChannel = new MessageChannel()

    return new Promise((resolve, reject) => {
      messageChannel.port1.onmessage = (event) => {
        if (event.data.success) {
          resolve(event.data)
        } else {
          reject(new Error(event.data.error || 'Unknown error'))
        }
      }

      registration.active?.postMessage(message, [messageChannel.port2])
    })
  }

  // 获取缓存信息
  async function getCacheInfo() {
    isLoading.value = true
    error.value = null
    
    try {
      const response = await sendMessageToSW({ type: 'GET_CACHE_INFO' })
      cacheInfo.value = response.cacheInfo
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to get cache info'
      console.error('Failed to get cache info:', err)
    } finally {
      isLoading.value = false
    }
  }

  // 清理缓存
  async function cleanupCaches() {
    isLoading.value = true
    error.value = null
    
    try {
      const response = await sendMessageToSW({ type: 'CLEANUP_CACHES' })
      cacheInfo.value = response.cacheInfo
      return true
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to cleanup caches'
      console.error('Failed to cleanup caches:', err)
      return false
    } finally {
      isLoading.value = false
    }
  }

  // 格式化缓存大小
  function formatSize(bytes: number): string {
    if (bytes === 0) return '0 B'
    
    const k = 1024
    const sizes = ['B', 'KB', 'MB', 'GB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
  }

  // 获取缓存使用百分比（假设最大100MB）
  function getCacheUsagePercentage(totalSize: number): number {
    const maxSize = 100 * 1024 * 1024 // 100MB
    return Math.min((totalSize / maxSize) * 100, 100)
  }

  // 监听Service Worker消息
  function handleSWMessage(event: MessageEvent) {
    if (event.data && event.data.type === 'CACHE_SIZE_UPDATE') {
      cacheInfo.value = event.data.data
    }
  }

  onMounted(() => {
    // 获取初始缓存信息
    getCacheInfo()
    
    // 监听Service Worker消息
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.addEventListener('message', handleSWMessage)
    }
  })

  onUnmounted(() => {
    // 移除事件监听
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.removeEventListener('message', handleSWMessage)
    }
  })

  return {
    cacheInfo,
    isLoading,
    error,
    getCacheInfo,
    cleanupCaches,
    formatSize,
    getCacheUsagePercentage,
  }
}