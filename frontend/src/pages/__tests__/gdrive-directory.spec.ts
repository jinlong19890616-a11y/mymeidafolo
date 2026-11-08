import GdrivePage from '@/pages/gdrive.vue'
import Board from '@/views/gdrive/GdriveDirectoryBoard.vue'
import Dialog from '@/views/gdrive/GdriveDirectoryDialog.vue'
import { type GdriveDirectory, type GdriveScan } from '@/api/gdrive'
import { useUserStore } from '@/stores'
import { renderWithProviders } from '@tests/support/render'
import { server } from '@tests/support/msw/server'
import { gdriveSettings } from '@tests/support/fixtures/gdrive'
import { fireEvent, screen, waitFor, within } from '@testing-library/vue'
import { HttpResponse, http } from 'msw'
import { beforeEach, expect, it, vi } from 'vitest'

const url = '*/gdrive'
const initialState = { user: { superUser: true } }
const task: GdriveDirectory = {
  id: 1,
  revision: 1,
  name: '电影目录',
  source_path: '/downloads',
  archive_path: '/mount/media',
  enabled: false,
  recursive: true,
  remove_source: false,
  extensions: ['.mkv'],
  ignore_keywords: ['sample'],
  ignore_suffixes: [],
  min_size_mb: 0,
  settle_seconds: 300,
  schedule_enabled: false,
  interval_minutes: 30,
  next_run_at: null,
  last_scan_at: null,
  created_at: '2026-01-01',
  updated_at: '2026-01-01',
}
const scan: GdriveScan = {
  id: 10,
  directory_id: 1,
  directory_name: '电影目录',
  trigger: 'manual',
  dry_run: true,
  status: 'previewed',
  scanned: 2,
  matched: 1,
  skipped: 1,
  queued: 0,
  completed: 0,
  failed: 0,
  active: 0,
  error_message: null,
  created_at: '2026-01-01',
  finished_at: '2026-01-01',
}
beforeEach(() => {
  server.use(
    http.get(`${url}/tasks`, () => HttpResponse.json([])),
    http.get(`${url}/directories`, () => HttpResponse.json([task])),
    http.get(`${url}/scans`, () => HttpResponse.json([scan])),
    http.get(`${url}/settings`, () => HttpResponse.json(gdriveSettings)),
    http.get(`${url}/rules`, () => HttpResponse.json([])),
    http.get(`${url}/scans/:id`, ({ params }) =>
      HttpResponse.json({
        scan: { ...scan, id: Number(params.id) },
        total_items: 2,
        items: [
          {
            id: 1,
            source_path: '/downloads/movie.mkv',
            archive_path: '/mount/media/movie.mkv',
            size: 1024,
            reason: 'matched',
            task_id: null,
            status: null,
            error_message: null,
          },
        ],
      }),
    ),
  )
})

it('页面页签可进入目录任务并保留文件任务入口', async () => {
  await renderWithProviders(GdrivePage, { initialState })
  await fireEvent.click(screen.getByRole('tab', { name: '归档任务' }))
  expect(await screen.findByText('电影目录')).toBeVisible()
  await fireEvent.click(screen.getByRole('tab', { name: '文件任务' }))
  expect(await screen.findByText(/暂无归档任务/)).toBeVisible()
})

it('API 上传目录沿用独立上传开关，挂载关闭时仍可执行', async () => {
  server.use(
    http.get(`${url}/directories`, () =>
      HttpResponse.json([
        {
          ...task,
          enabled: true,
          upload_target: {
            account_ids: ['one', 'three'],
            drive_id: 'shared',
            folder_id: 'folder',
            path: '电影',
            strm_prefix: '',
          },
        },
      ]),
    ),
    http.get(`${url}/settings`, () => HttpResponse.json({ ...gdriveSettings, enabled: false })),
    http.get(`${url}/upload/settings`, () =>
      HttpResponse.json({
        success: true,
        data: {
          enabled: true,
          concurrency: 4,
          chunk_size_mb: 16,
          accounts: [],
        },
      }),
    ),
  )
  await renderWithProviders(Board, { initialState })
  await screen.findByText('Google Drive · 电影 · 2 个账号')
  await waitFor(() => expect(screen.getByRole('button', { name: '扫描并归档' })).toBeEnabled())
  expect(screen.getByText('不生成 STRM')).toBeVisible()
})

it('保存目录和过滤规则不会触发扫描，新任务默认停用并保留源文件', async () => {
  const requests: unknown[] = []
  const start = vi.fn(() => HttpResponse.json({ success: true }))
  server.use(
    http.post(`${url}/directories`, async ({ request }) => {
      requests.push(await request.json())
      return HttpResponse.json({ success: true })
    }),
    http.post(`${url}/directories/:id/scan`, start),
  )
  const rendered = await renderWithProviders(Dialog)
  await fireEvent.update(screen.getByLabelText('任务名称'), ' 新目录 ')
  await fireEvent.update(screen.getByLabelText('源目录'), ' /downloads ')
  await fireEvent.update(screen.getByLabelText('归档目录'), '/mount/media')
  await fireEvent.click(screen.getByRole('tab', { name: '2 · 归档规则与过滤' }))
  await fireEvent.update(screen.getByLabelText('媒体扩展名白名单'), '.mkv，.mp4')
  await fireEvent.update(screen.getByLabelText('忽略关键词'), 'sample; trailer')
  await fireEvent.update(screen.getByLabelText('最小文件大小（MiB）'), '100')
  await fireEvent.click(screen.getByRole('button', { name: '保存目录任务' }))
  await waitFor(() => expect(rendered.emitted().saved).toHaveLength(1))
  expect(requests).toEqual([
    expect.objectContaining({
      name: '新目录',
      source_path: '/downloads',
      extensions: ['.mkv', '.mp4'],
      ignore_keywords: ['sample', 'trailer'],
      min_size_mb: 100,
      enabled: false,
      remove_source: false,
    }),
  ])
  expect(start).not.toHaveBeenCalled()
})

it('预览发送 dry_run 并显示过滤原因和文件上限', async () => {
  const requests: unknown[] = []
  server.use(
    http.post(`${url}/directories/1/scan`, async ({ request }) => {
      requests.push(await request.json())
      return HttpResponse.json({ success: true, data: { id: 10 } })
    }),
  )
  await renderWithProviders(Board, { initialState })
  await screen.findByText('电影目录')
  await fireEvent.click(screen.getByRole('button', { name: '只读扫描预览' }))
  const dialog = await screen.findByRole('dialog', { name: '扫描详情' })
  expect(await within(dialog).findByText('/downloads/movie.mkv')).toBeVisible()
  expect(within(dialog).getByText(/符合规则/)).toBeVisible()
  expect(within(dialog).getByText(/最多显示前 500 项/)).toBeVisible()
  expect(requests).toEqual([{ dry_run: true }])
})

it('实际扫描需确认，启用任务不能删除', async () => {
  const requests: unknown[] = []
  server.use(
    http.get(`${url}/directories`, () => HttpResponse.json([{ ...task, enabled: true }])),
    http.post(`${url}/directories/1/scan`, async ({ request }) => {
      requests.push(await request.json())
      return HttpResponse.json({ success: false, message: '已有活动批次' })
    }),
  )
  await renderWithProviders(Board, { initialState })
  await screen.findByText('电影目录')
  expect(screen.getByRole('button', { name: '删除目录任务' })).toBeDisabled()
  await fireEvent.click(screen.getByRole('button', { name: '扫描并归档' }))
  const dialog = await screen.findByRole('dialog', { name: '扫描并归档' })
  expect(requests).toHaveLength(0)
  await fireEvent.click(within(dialog).getByRole('button', { name: '确认' }))
  expect(await screen.findByText('已有活动批次')).toBeVisible()
  expect(requests).toEqual([{ dry_run: false }])
})

it('保存结果丢失后阻止重复提交，保留输入', async () => {
  server.use(http.post(`${url}/directories`, () => HttpResponse.error()))
  await renderWithProviders(Dialog)
  await fireEvent.update(screen.getByLabelText('任务名称'), '新目录')
  await fireEvent.update(screen.getByLabelText('源目录'), '/downloads')
  await fireEvent.update(screen.getByLabelText('归档目录'), '/mount/media')
  await fireEvent.click(screen.getByRole('button', { name: '保存目录任务' }))
  expect(await screen.findByText(/未收到操作结果/)).toBeVisible()
  expect(screen.getByRole('button', { name: '保存目录任务' })).toBeDisabled()
})

it('列表加载失败支持刷新恢复，撤销超级管理员权限关闭编辑器', async () => {
  server.use(http.get(`${url}/directories`, () => HttpResponse.json({}, { status: 500 })))
  const rendered = await renderWithProviders(Board, { initialState })
  expect(await screen.findByText(/目录任务或扫描历史加载失败/)).toBeVisible()
  server.use(http.get(`${url}/directories`, () => HttpResponse.json([task])))
  await fireEvent.click(screen.getByRole('button', { name: '刷新' }))
  await screen.findByText('电影目录')
  await waitFor(() => expect(screen.queryByText(/目录任务或扫描历史加载失败/)).not.toBeInTheDocument())
  await fireEvent.click(screen.getByRole('button', { name: '编辑目录任务' }))
  await screen.findByRole('dialog', { name: '编辑目录任务' })
  useUserStore(rendered.pinia).superUser = false
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
})

it('删除停用配置保留历史，挂载配置只更新显示字段', async () => {
  const remove = vi.fn(() => HttpResponse.json({ success: true }))
  let settings: unknown
  server.use(
    http.delete(`${url}/directories/1`, remove),
    http.put(`${url}/settings`, async ({ request }) => {
      settings = await request.json()
      return HttpResponse.json({ success: true })
    }),
  )
  await renderWithProviders(Board, { initialState })
  await screen.findByText('电影目录')
  await fireEvent.click(screen.getByRole('button', { name: '删除目录任务' }))
  const confirm = await screen.findByRole('dialog', { name: '删除目录任务' })
  await fireEvent.click(within(confirm).getByRole('button', { name: '确认' }))
  await waitFor(() => expect(remove).toHaveBeenCalledTimes(1))
  await fireEvent.click(screen.getByRole('button', { name: '挂载与 STRM 配置' }))
  const dialog = await screen.findByRole('dialog', { name: '挂载与 STRM 配置' })
  await within(dialog).findByLabelText('挂载目录')
  await fireEvent.update(within(dialog).getByLabelText('媒体服务器 STRM 路径前缀'), '/media')
  await fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))
  await waitFor(() => expect(settings).toEqual({ ...gdriveSettings, strm_path_prefix: '/media' }))
})
