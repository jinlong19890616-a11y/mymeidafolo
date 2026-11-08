import api from '@/api'

export const GDRIVE_STATUSES = [
  'pending',
  'uploading',
  'archiving',
  'generating_strm',
  'completed',
  'failed',
  'cancelled',
] as const
export type GdriveStatus = (typeof GDRIVE_STATUSES)[number]

export interface GdriveTask {
  id: number
  backend?: 'mount' | 'api'
  upload_account_id?: string | null
  remote_file_id?: string | null
  upload_target?: GdriveUploadTarget | null
  src_path: string
  archive_path: string | null
  strm_path: string | null
  source_size: number
  uploaded_size: number
  status: string
  stage: string
  progress: number
  error_message: string | null
  retry_count: number
  remove_source: boolean
  media_type: string | null
  title: string | null
  transfer_history_id: number | null
  created_at: string
  updated_at: string
}

export interface GdriveHistory {
  id: number
  task_id: number
  from_status: string | null
  to_status: string
  from_stage: string | null
  to_stage: string
  event_type: string
  error_message: string | null
  retry_count: number
  detail: string | null
  created_at: string
}

export interface GdrivePreflight {
  ready: boolean
  source_path: string
  mount_path: string
  archive_path: string | null
  strm_path: string | null
  source_size: number | null
  checks: { name: string; passed: boolean; level: string; message: string }[]
}

export interface GdriveSettings {
  enabled: boolean
  mount_path: string
  archive_path: string
  strm_path_prefix: string
  remove_source: boolean
  version_suffix: string
  source_root: string
  copy_chunk_size: number
}

export interface GdriveTaskCreate {
  source_path: string
  archive_path?: string
  remove_source?: boolean
}

export interface GdriveDirectoryConfig {
  name: string
  source_path: string
  archive_path: string
  enabled: boolean
  recursive: boolean
  remove_source: boolean
  extensions: string[]
  ignore_keywords: string[]
  ignore_suffixes: string[]
  min_size_mb: number
  settle_seconds: number
  schedule_enabled: boolean
  interval_minutes: number
  rule_id?: number | null
  classification_mode?: 'inherit' | 'moviepilot' | 'custom'
  category_policy?: GdriveCategoryPolicy
  transfer_enabled?: boolean
  strm_root?: string
  upload_target?: GdriveUploadTarget | null
}

export interface GdriveCategoryPolicy {
  movie: { name: string; conditions: Record<string, string> }[]
  tv: { name: string; conditions: Record<string, string> }[]
  unmatched: 'skip' | 'fallback' | 'keep'
  fallback: string
  path_mode?: 'media_type' | 'relative'
}

export interface GdriveDirectory extends GdriveDirectoryConfig {
  id: number
  revision: number
  next_run_at: string | null
  last_scan_at: string | null
  created_at: string
  updated_at: string
}

export interface GdriveScan {
  id: number
  directory_id: number
  directory_name: string
  trigger: string
  dry_run: boolean
  status: string
  scanned: number
  matched: number
  skipped: number
  queued: number
  completed: number
  failed: number
  active: number
  error_message: string | null
  created_at: string
  finished_at: string | null
}

export interface GdriveScanDetail {
  scan: GdriveScan
  config?: GdriveDirectoryConfig
  rule?: GdriveRule | null
  total_items: number
  items: {
    id: number
    source_path: string
    archive_path: string | null
    size: number
    reason: string
    task_id: number | null
    status: string | null
    stage?: string | null
    progress?: number
    error_message: string | null
  }[]
}

export interface GdriveRuleConfig {
  name: string
  media_type: 'auto' | 'movie' | 'tv'
  movie_directory: string
  movie_filename: string
  tv_directory: string
  tv_filename: string
  categories: string[]
  category_policy?: GdriveCategoryPolicy | null
}

export interface GdriveRule extends GdriveRuleConfig {
  id: number
  revision: number
  updated_at: string
}

export const gdriveRuleApi = {
  list: (signal: AbortSignal) => api.get<never, GdriveRule[]>('gdrive/rules', { signal }),
  save: (value: GdriveRuleConfig, id?: number) =>
    id
      ? api.put<never, GdriveResponse<GdriveRule>>(`gdrive/rules/${id}`, value)
      : api.post<never, GdriveResponse<GdriveRule>>('gdrive/rules', value),
  remove: (id: number) => api.delete<never, GdriveResponse<never>>(`gdrive/rules/${id}`),
  preview: (rule: GdriveRuleConfig, filename: string, signal: AbortSignal, media: Record<string, unknown> = {}) =>
    api.post<never, GdriveResponse<{ path: string }>>('gdrive/rules/preview', { rule, filename, media }, { signal }),
  importCategories: (content: string, policy: GdriveCategoryPolicy, signal: AbortSignal) =>
    api.post<never, GdriveResponse<{ policy: GdriveCategoryPolicy; warnings: string[] }>>(
      'gdrive/categories/import',
      { content, policy },
      { signal },
    ),
}

interface GdriveResponse<T> {
  success: boolean
  message?: string
  data?: T
}

export interface GdriveUploadAccount {
  id: string
  name: string
  enabled: boolean
  concurrency: number
}

export interface GdriveUploadSettings {
  enabled: boolean
  concurrency: number
  chunk_size_mb: number
  notifications_enabled?: boolean
  daily_report_enabled?: boolean
  notification_source?: string
  accounts: GdriveUploadAccount[]
  credential_status?: Record<string, boolean>
}

export interface GdriveUploadTarget {
  account_ids: string[]
  drive_id: string
  folder_id: string
  path: string
  archive_folder_id?: string
  archive_path?: string
  strm_prefix: string
  metadata_enabled?: boolean
  refresh_server?: string
  refresh_library_id?: string
}

export const gdriveUploadApi = {
  settings: (signal: AbortSignal) =>
    api.get<never, GdriveResponse<GdriveUploadSettings>>('gdrive/upload/settings', { signal }),
  save: (value: GdriveUploadSettings) => api.put<never, GdriveResponse<never>>('gdrive/upload/settings', value),
  credential: (id: string, value: { token: string; client_id: string; client_secret: string }) =>
    api.put<never, GdriveResponse<never>>(`gdrive/upload/accounts/${encodeURIComponent(id)}/credential`, value),
  check: (value: GdriveUploadTarget) =>
    api.post<never, GdriveResponse<{ id: string; success: boolean; message: string }[]>>('gdrive/upload/check', value),
  create: (value: { source_path: string; target: GdriveUploadTarget; strm_root: string; remove_source: boolean }) =>
    api.post<never, GdriveResponse<{ id: number }>>('gdrive/upload/tasks', value),
  retry: (id: number) => api.post<never, GdriveResponse<never>>(`gdrive/tasks/${id}/retry`),
  cancel: (id: number) => api.post<never, GdriveResponse<never>>(`gdrive/tasks/${id}/cancel`),
}

export const gdriveDirectoryApi = {
  previewCategory: (
    config: GdriveDirectoryConfig,
    filename: string,
    media: Record<string, unknown>,
    signal: AbortSignal,
  ) =>
    api.post<never, GdriveResponse<{ path: string; policy: GdriveCategoryPolicy }>>(
      'gdrive/categories/preview',
      { config, filename, media },
      { signal },
    ),
  list: (signal: AbortSignal) => api.get<never, GdriveDirectory[]>('gdrive/directories', { signal }),
  save: (value: GdriveDirectoryConfig, id?: number) =>
    id
      ? api.put<never, GdriveResponse<GdriveDirectory>>(`gdrive/directories/${id}`, value)
      : api.post<never, GdriveResponse<GdriveDirectory>>('gdrive/directories', value),
  remove: (id: number) => api.delete<never, GdriveResponse<never>>(`gdrive/directories/${id}`),
  scan: (id: number, dryRun = true) =>
    api.post<never, GdriveResponse<{ id: number }>>(`gdrive/directories/${id}/scan`, { dry_run: dryRun }),
  scans: (signal: AbortSignal, directoryId?: number) =>
    api.get<never, GdriveScan[]>('gdrive/scans', { params: { directory_id: directoryId, limit: 100 }, signal }),
  detail: (id: number, signal: AbortSignal) => api.get<never, GdriveScanDetail>(`gdrive/scans/${id}`, { signal }),
  settings: (value: GdriveSettings) => api.put<never, GdriveResponse<GdriveSettings>>('gdrive/settings', value),
}

export const gdriveApi = {
  get: (id: number, signal: AbortSignal) => api.get<never, GdriveTask>(`gdrive/tasks/${id}`, { signal }),
  /** 查询最近任务，允许页面在离开时中止读取。 */
  list: (signal: AbortSignal) => api.get<never, GdriveTask[]>('gdrive/tasks', { params: { limit: 100 }, signal }),
  /** 读取当前配置，用于展示最终源文件策略。 */
  settings: (signal: AbortSignal) => api.get<never, GdriveSettings>('gdrive/settings', { signal }),
  /** 只读检查路径与配置，不启动任务。 */
  preflight: (paths: Pick<GdriveTaskCreate, 'source_path' | 'archive_path'>, signal: AbortSignal) =>
    api.post<never, GdrivePreflight>('gdrive/preflight', paths, { signal }),
  /** 创建并立即启动任务，响应丢失时应先查询列表确认。 */
  create: (value: GdriveTaskCreate) =>
    api.post<never, { success: boolean; message?: string; data?: { id: number } }>('gdrive/tasks', value),
  /** 查询任务最近的状态历史。 */
  history: (id: number, signal: AbortSignal) =>
    api.get<never, GdriveHistory[]>(`gdrive/tasks/${id}/history`, { params: { limit: 200 }, signal }),
}

/** 未识别的新状态也继续刷新，避免客户端版本较旧时停止观察任务。 */
export function isGdriveTaskActive(task: GdriveTask): boolean {
  return !['completed', 'failed', 'cancelled'].includes(task.status)
}

/** 优先显示媒体标题，无标题时使用源文件名。 */
export function gdriveTaskTitle(task: GdriveTask): string {
  return task.title || task.src_path.split(/[\\/]/).pop() || task.src_path
}

/** 显示二进制文件大小，进度含义由界面单独说明。 */
export function gdriveBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}
