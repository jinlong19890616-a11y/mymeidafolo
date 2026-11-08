import type { GdriveHistory, GdrivePreflight, GdriveSettings, GdriveTask } from '@/api/gdrive'

export const gdriveSettings: GdriveSettings = {
  enabled: true,
  mount_path: '/mnt/gdrive',
  archive_path: '/archive',
  strm_path_prefix: '/media',
  remove_source: true,
  version_suffix: 'version',
  source_root: '/downloads',
  copy_chunk_size: 10485760,
}

/** 构造离线任务，避免界面测试读取真实文件或启动归档。 */
export function gdriveTask(overrides: Partial<GdriveTask> = {}): GdriveTask {
  return {
    id: 1,
    src_path: '/downloads/Example.mkv',
    archive_path: '/archive/Example.mkv',
    strm_path: '/archive/Example.strm',
    source_size: 1073741824,
    uploaded_size: 536870912,
    status: 'uploading',
    stage: 'uploading',
    progress: 50,
    error_message: null,
    retry_count: 0,
    remove_source: true,
    media_type: null,
    title: '示例电影',
    transfer_history_id: null,
    created_at: '2026-11-08 10:00:00',
    updated_at: '2026-11-08 10:02:00',
    ...overrides,
  }
}

export const gdrivePreflight: GdrivePreflight = {
  ready: true,
  source_path: '/downloads/Example.mkv',
  mount_path: '/mnt/gdrive',
  archive_path: '/archive/Example.mkv',
  strm_path: '/archive/Example.strm',
  source_size: 1073741824,
  checks: [{ name: 'source', passed: true, level: 'info', message: '源文件检查通过' }],
}

export const gdriveHistory: GdriveHistory[] = [
  {
    id: 1,
    task_id: 1,
    from_status: 'pending',
    to_status: 'uploading',
    from_stage: 'pending',
    to_stage: 'uploading',
    event_type: 'started',
    error_message: null,
    retry_count: 0,
    detail: '开始复制源文件',
    created_at: '2026-11-08 10:01:00',
  },
]
