export {}

declare global {
  /**
   * Background SyncManager interface as per the Web Background Sync API.
   */
  interface SyncManager {
    /**
     * Registers a one-off sync event with the provided tag.
     */
    register(tag: string): Promise<void>
  }

  /**
   * Extension of ServiceWorkerRegistration to include the SyncManager.
   */
  interface ServiceWorkerRegistration {
    /**
     * The SyncManager for background sync operations.
     */
    readonly sync: SyncManager
  }

  /**
   * The event fired when a background sync is triggered.
   */
  interface SyncEvent extends ExtendableEvent {
    readonly tag: string
    readonly lastChance: boolean
  }

  /**
   * Extend ServiceWorkerGlobalScope event map to include the sync event type.
   */
  interface ServiceWorkerGlobalScopeEventMap {
    'sync': SyncEvent
  }
}