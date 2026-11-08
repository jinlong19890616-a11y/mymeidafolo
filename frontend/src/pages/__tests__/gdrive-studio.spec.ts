import PathField from '@/views/gdrive/GdrivePathField.vue'
import RuleDialog from '@/views/gdrive/GdriveRuleDialog.vue'
import Rules from '@/views/gdrive/GdriveRules.vue'
import Dialog from '@/views/gdrive/GdriveDirectoryDialog.vue'
import Flow from '@/views/gdrive/GdriveFlow.vue'
import { renderWithProviders } from '@tests/support/render'
import { server } from '@tests/support/msw/server'
import { fireEvent, screen, waitFor, within } from '@testing-library/vue'
import { HttpResponse, http } from 'msw'
import { expect, it, vi } from 'vitest'
import type { GdriveDirectoryConfig, GdriveScan } from '@/api/gdrive'

const url = '*/gdrive'
const rule = {
  id: 1,
  revision: 1,
  updated_at: '2026-01-01',
  name: '媒体库规则',
  media_type: 'auto',
  movie_directory: '{{ title }} ({{ year }})',
  movie_filename: '{{ title }}',
  tv_directory: '{{ title }}/Season {{ season }}',
  tv_filename: '{{ season_episode }}',
  categories: ['media_type'],
}

it('目录浏览只显示文件夹，选择当前目录并支持返回上级', async () => {
  const calls: unknown[] = []
  server.use(
    http.post('*/storage/list', async ({ request }) => {
      const value = (await request.json()) as { path: string }
      calls.push(value)
      return HttpResponse.json(
        value.path === '/'
          ? [
              { name: 'downloads', path: '/downloads/', type: 'dir' },
              { name: 'movie.mkv', path: '/movie.mkv', type: 'file' },
            ]
          : [],
      )
    }),
  )
  const rendered = await renderWithProviders(PathField, { props: { modelValue: '', label: '源目录' } })
  await fireEvent.click(screen.getByRole('button', { name: '选择目录 · 源目录' }))
  const dialog = await screen.findByRole('dialog', { name: '选择目录 · 源目录' })
  expect(await within(dialog).findByText('downloads')).toBeVisible()
  expect(within(dialog).queryByText('movie.mkv')).not.toBeInTheDocument()
  await fireEvent.click(within(dialog).getByText('downloads'))
  expect(await within(dialog).findByText('当前目录没有子目录')).toBeVisible()
  await fireEvent.click(within(dialog).getByRole('button', { name: '返回上级' }))
  await within(dialog).findByText('downloads')
  await fireEvent.click(within(dialog).getByText('downloads'))
  await within(dialog).findByText('当前目录没有子目录')
  await fireEvent.click(within(dialog).getByRole('button', { name: '使用当前目录' }))
  expect(rendered.emitted()['update:modelValue']).toEqual([['/downloads']])
  expect(calls).toHaveLength(4)
})

it('目录读取失败不可选取，重试后恢复', async () => {
  server.use(http.post('*/storage/list', () => HttpResponse.error()))
  await renderWithProviders(PathField, { props: { modelValue: '/', label: '归档目录' } })
  await fireEvent.click(screen.getByRole('button', { name: '选择目录 · 归档目录' }))
  const dialog = await screen.findByRole('dialog')
  expect(await within(dialog).findByText(/目录读取失败/)).toBeVisible()
  expect(within(dialog).getByRole('button', { name: '使用当前目录' })).toBeDisabled()
  server.use(http.post('*/storage/list', () => HttpResponse.json([])))
  await fireEvent.click(within(dialog).getByRole('button', { name: '重试' }))
  await waitFor(() => expect(within(dialog).getByRole('button', { name: '使用当前目录' })).toBeEnabled())
})

it('规则编辑预览完整结构，电影与剧集分开保存且不扫描', async () => {
  const save = vi.fn(async ({ request }) => HttpResponse.json({ success: true, data: await request.json() }))
  const scan = vi.fn(() => HttpResponse.json({ success: true }))
  server.use(
    http.post(`${url}/rules/preview`, async ({ request }) => {
      const body = (await request.json()) as { filename: string }
      return HttpResponse.json({
        success: true,
        data: {
          path: body.filename.includes('S01')
            ? '电视剧/Example Show/Season 01/S01E02.mkv'
            : '电影/Example Movie (2024)/Example Movie.mkv',
        },
      })
    }),
    http.post(`${url}/rules`, save),
    http.post(`${url}/directories/:id/scan`, scan),
  )
  const rendered = await renderWithProviders(RuleDialog)
  expect(await screen.findByText('Example Movie (2024)')).toBeVisible()
  await fireEvent.update(screen.getByLabelText('规则名称'), '测试规则')
  await fireEvent.click(screen.getByRole('tab', { name: '电视剧' }))
  expect(await screen.findByText('Season 01')).toBeVisible()
  expect(await screen.findByText('S01E02.mkv')).toBeVisible()
  await fireEvent.update(screen.getByLabelText('文件命名模板'), '{{ title }} - {{ season_episode }}')
  await fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(rendered.emitted().saved).toHaveLength(1))
  expect(save).toHaveBeenCalledOnce()
  expect(scan).not.toHaveBeenCalled()
})

it('目录任务可引用规则并在确认页显示流程和源文件策略', async () => {
  let payload: unknown
  server.use(
    http.get(`${url}/rules`, () => HttpResponse.json([rule])),
    http.post(`${url}/directories`, async ({ request }) => {
      payload = await request.json()
      return HttpResponse.json({ success: true })
    }),
  )
  await renderWithProviders(Dialog)
  await fireEvent.update(screen.getByLabelText('任务名称'), '电影任务')
  await fireEvent.update(screen.getByLabelText('源目录'), '/downloads')
  await fireEvent.update(screen.getByLabelText('归档目录'), '/mount/media')
  await fireEvent.click(screen.getByRole('tab', { name: '2 · 归档规则与过滤' }))
  await fireEvent.mouseDown(screen.getByLabelText('归档规则'))
  await fireEvent.click(await screen.findByRole('option', { name: '媒体库规则' }))
  await fireEvent.click(screen.getByRole('tab', { name: '4 · 流程与确认' }))
  expect(screen.getByText('归档规则: 媒体库规则')).toBeVisible()
  expect(screen.getByRole('list', { name: '归档流程' })).toBeVisible()
  expect(screen.getByText(/只读预览在路径计算后结束/)).toBeVisible()
  await fireEvent.click(screen.getByRole('button', { name: '保存目录任务' }))
  await waitFor(() =>
    expect(payload).toEqual(expect.objectContaining({ rule_id: 1, enabled: false, remove_source: false })),
  )
})

it('规则被任务引用时显示后端拒绝删除原因', async () => {
  server.use(
    http.get(`${url}/rules`, () => HttpResponse.json([rule])),
    http.delete(`${url}/rules/1`, () => HttpResponse.json({ success: false, message: '规则仍被目录任务引用' })),
  )
  await renderWithProviders(Rules, { initialState: { user: { superUser: true } } })
  await screen.findByText('媒体库规则')
  await fireEvent.click(screen.getByRole('button', { name: '删除' }))
  await fireEvent.click(screen.getByRole('button', { name: '确认' }))
  expect(await screen.findByText('规则仍被目录任务引用')).toBeVisible()
})

it('自定义分类按顺序编辑、离线预览并保存独立 STRM 根目录', async () => {
  let payload: GdriveDirectoryConfig | undefined
  let preview: { config: GdriveDirectoryConfig } | undefined
  server.use(
    http.get(`${url}/rules`, () => HttpResponse.json([])),
    http.post(`${url}/categories/preview`, async ({ request }) => {
      preview = (await request.json()) as { config: GdriveDirectoryConfig }
      return HttpResponse.json({
        success: true,
        data: {
          path: '电影/华语动画/Example Movie (2024)/Example Movie (2024).mkv',
          policy: preview.config.category_policy,
        },
      })
    }),
    http.post(`${url}/directories`, async ({ request }) => {
      payload = (await request.json()) as GdriveDirectoryConfig
      return HttpResponse.json({ success: true })
    }),
  )
  await renderWithProviders(Dialog)
  await fireEvent.update(screen.getByLabelText('任务名称'), '分类任务')
  await fireEvent.update(screen.getByLabelText('源目录'), '/downloads')
  await fireEvent.update(screen.getByLabelText('归档目录'), '/mount/media')
  await fireEvent.update(screen.getByLabelText('本地 STRM 根目录（可选）'), '/library')
  await fireEvent.click(screen.getByRole('tab', { name: '2 · 归档规则与过滤' }))
  await fireEvent.mouseDown(screen.getByLabelText('二级分类策略'))
  await fireEvent.click(await screen.findByRole('option', { name: '自定义网盘分类策略' }))
  await fireEvent.click(screen.getByRole('button', { name: '添加分类' }))
  await fireEvent.update(screen.getByLabelText('分类名称'), '华语动画')
  await fireEvent.update(screen.getByLabelText('原始语种（zh,en…）'), 'zh,cn')
  await fireEvent.update(screen.getByLabelText('内容类型编号（16 动画，99 纪录片）'), '16')
  await fireEvent.click(screen.getByRole('button', { name: '添加分类' }))
  await fireEvent.update(screen.getAllByLabelText('分类名称')[1], '其他电影')
  await fireEvent.click(screen.getAllByRole('button', { name: '上移分类' })[1])
  expect(screen.getAllByLabelText('分类名称')[0]).toHaveValue('其他电影')
  await fireEvent.click(screen.getAllByRole('button', { name: '下移分类' })[0])
  await fireEvent.click(screen.getByRole('button', { name: '离线分类预览' }))
  const previewButtons = await screen.findAllByRole('button', { name: '离线分类预览' })
  await fireEvent.click(previewButtons[1])
  expect(await screen.findByText('电影/华语动画/Example Movie (2024)/Example Movie (2024).mkv')).toBeVisible()
  expect(preview?.config.category_policy?.movie[0]).toEqual({
    name: '华语动画',
    conditions: { original_language: 'zh,cn', genre_ids: '16' },
  })
  await fireEvent.click(screen.getByRole('tab', { name: '4 · 流程与确认' }))
  expect(screen.getByText('二级分类策略: 自定义网盘分类策略')).toBeVisible()
  await fireEvent.click(screen.getByRole('button', { name: '保存目录任务' }))
  await waitFor(() =>
    expect(payload).toEqual(
      expect.objectContaining({
        classification_mode: 'custom',
        transfer_enabled: true,
        strm_root: '/library',
        enabled: false,
      }),
    ),
  )
  expect(payload?.category_policy?.movie.map(item => item.name)).toEqual(['华语动画', '其他电影'])
})

it('失败阶段保留在实际归档阶段，预览不会显示后续阶段已完成', async () => {
  const rendered = await renderWithProviders(Flow, { props: { stage: 'archiving', status: 'failed' } })
  expect(screen.getByText('云端分类归档').closest('li')).toHaveClass('halted')
  expect(screen.getByText('生成 STRM').closest('li')).not.toHaveClass('done')
  rendered.unmount()
  await renderWithProviders(Flow, { props: { preview: true } })
  expect(screen.getByText('上传至暂存目录').closest('li')).toHaveClass('muted')
})

it('等待中文件不提前显示复制，扫描失败不显示路径计算完成', async () => {
  const rendered = await renderWithProviders(Flow, { props: { stage: 'pending', status: 'pending' } })
  expect(screen.getByText('上传至暂存目录').closest('li')).not.toHaveClass('active')
  rendered.unmount()
  await renderWithProviders(Flow, { props: { scan: { dry_run: true, status: 'failed' } as GdriveScan } })
  expect(screen.getByText('扫描目录').closest('li')).toHaveClass('halted')
  expect(screen.getByText('计算归档路径').closest('li')).not.toHaveClass('done')
})
