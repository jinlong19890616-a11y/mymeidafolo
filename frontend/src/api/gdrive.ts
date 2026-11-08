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

export const gdriveApi = {
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
