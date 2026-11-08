import Upload from '@/views/gdrive/GdriveUpload.vue'
import { renderWithProviders } from '@tests/support/render'
import { server } from '@tests/support/msw/server'
import { gdriveTask } from '@tests/support/fixtures/gdrive'
import { fireEvent, screen, waitFor } from '@testing-library/vue'
import { HttpResponse, http } from 'msw'
import { beforeEach, expect, it } from 'vitest'

const url = '*/gdrive'
const config = {
  enabled: true,
  concurrency: 4,
  chunk_size_mb: 16,
  accounts: [1, 2, 3, 4].map(id => ({ id: String(id), name: `谷歌账号 ${id}`, enabled: true, concurrency: 1 })),
  credential_status: { '1': true, '2': true, '3': true, '4': true },
}
const initialState = { user: { superUser: true } }

beforeEach(() => {
  server.use(
    http.get(`${url}/upload/settings`, () => HttpResponse.json({ success: true, data: config })),
    http.get(`${url}/tasks`, () => HttpResponse.json([])),
  )
})

async function fillUpload() {
  await fireEvent.update(await screen.findByLabelText('服务器源文件路径'), '/media/movie.mkv')
  await fireEvent.update(screen.getByLabelText('团队共享盘 ID'), 'shared-drive')
  await fireEvent.update(screen.getByLabelText('上传暂存文件夹 ID（drmedia）'), 'folder')
  await fireEvent.mouseDown(screen.getByLabelText('参与上传的账号'))
  await fireEvent.click(await screen.findByRole('option', { name: '谷歌账号 1' }))
  await fireEvent.click(await screen.findByRole('option', { name: '谷歌账号 3' }))
  await fireEvent.keyDown(screen.getByLabelText('参与上传的账号'), { key: 'Escape' })
}

it('保存上传账号和两层并发配置，不创建上传任务', async () => {
  const requests: unknown[] = []
  server.use(
    http.put(`${url}/upload/settings`, async ({ request }) => {
      requests.push(await request.json())
      return HttpResponse.json({ success: true })
    }),
  )
  await renderWithProviders(Upload, { initialState })
  await screen.findByLabelText('总上传并发')
  await fireEvent.update(screen.getByLabelText('总上传并发'), '8')
  await fireEvent.update(screen.getAllByLabelText('该账号并发')[0], '2')
  await fireEvent.click(screen.getByRole('button', { name: '保存上传配置' }))
  await screen.findByText('上传配置已保存。')
  expect(requests).toEqual([
    {
      ...config,
      concurrency: 8,
      credential_status: undefined,
      accounts: config.accounts.map((account, index) => ({ ...account, concurrency: index ? 1 : 2 })),
    },
  ])
})

it('选择两个账号直接上传，默认保源且不调用挂载创建接口', async () => {
  const requests: unknown[] = []
  server.use(
    http.post(`${url}/upload/tasks`, async ({ request }) => {
      requests.push(await request.json())
      return HttpResponse.json({ success: true, data: { id: 9 } })
    }),
  )
  await renderWithProviders(Upload, { initialState })
  await fillUpload()
  await fireEvent.click(screen.getByRole('button', { name: '开始上传' }))
  await screen.findByText('直接上传任务已入队。')
  expect(requests).toEqual([
    {
      source_path: '/media/movie.mkv',
      strm_root: '',
      remove_source: false,
      target: { account_ids: ['1', '3'], drive_id: 'shared-drive', folder_id: 'folder', path: '', strm_prefix: '' },
    },
  ])
})

it('Token 只写提交后清空，返回页面只显示已配置状态', async () => {
  const requests: unknown[] = []
  server.use(
    http.put(`${url}/upload/accounts/1/credential`, async ({ request }) => {
      requests.push(await request.json())
      return HttpResponse.json({ success: true })
    }),
  )
  await renderWithProviders(Upload, { initialState })
  await screen.findByLabelText('总上传并发')
  await fireEvent.click(screen.getAllByRole('button', { name: '设置 Token' })[0])
  await fireEvent.update(screen.getByLabelText('Token JSON（只写）'), '{"access_token":"private-test"}')
  await fireEvent.click(screen.getByRole('button', { name: '保存 Token' }))
  await screen.findByText('账号 Token 已保存，页面不会回显。')
  expect(requests).toEqual([{ token: '{"access_token":"private-test"}', client_id: '', client_secret: '' }])
  await waitFor(() => expect(screen.queryByLabelText('Token JSON（只写）')).not.toBeInTheDocument())
  await fireEvent.click(screen.getAllByRole('button', { name: '设置 Token' })[0])
  expect(screen.getByLabelText('Token JSON（只写）')).toHaveValue('')
})

it('分别提交 drmedia 暂存和 media 云端归档目标', async () => {
  const requests: unknown[] = []
  server.use(http.post(`${url}/upload/tasks`, async ({ request }) => {
    requests.push(await request.json())
    return HttpResponse.json({ success: true, data: { id: 11 } })
  }))
  await renderWithProviders(Upload, { initialState })
  await fillUpload()
  await fireEvent.update(screen.getByLabelText('上传暂存相对路径'), '整理/movie.mkv')
  await fireEvent.update(screen.getByLabelText('云端归档文件夹 ID（media）'), 'media-folder')
  await fireEvent.update(await screen.findByLabelText('云端归档相对路径'), '电影/国产/movie.mkv')
  await fireEvent.click(screen.getByRole('button', { name: '开始上传' }))
  await screen.findByText('直接上传任务已入队。')
  expect(requests).toEqual([expect.objectContaining({ target: expect.objectContaining({
    folder_id: 'folder', path: '整理/movie.mkv', archive_folder_id: 'media-folder', archive_path: '电影/国产/movie.mkv',
  }) })])
})

it('提交本地元数据开关和明确的 Emby 扫库目标', async () => {
  const requests: unknown[] = []
  server.use(
    http.post(`${url}/upload/tasks`, async ({ request }) => {
      requests.push(await request.json())
      return HttpResponse.json({ success: true, data: { id: 10 } })
    }),
  )
  await renderWithProviders(Upload, { initialState })
  await fillUpload()
  await fireEvent.click(screen.getByLabelText('生成 STRM 后准备本地元数据'))
  await fireEvent.update(screen.getByLabelText('完成后扫库的媒体服务器名称'), '本机 Emby')
  await fireEvent.update(screen.getByLabelText('Emby 媒体库 ID'), '5')
  await fireEvent.click(screen.getByRole('button', { name: '开始上传' }))
  await screen.findByText('直接上传任务已入队。')
  expect(requests).toEqual([
    expect.objectContaining({
      target: expect.objectContaining({ metadata_enabled: true, refresh_server: '本机 Emby', refresh_library_id: '5' }),
      remove_source: false,
    }),
  ])
})

it('上传队列显示账号、云端文件编号和失败重试入口', async () => {
  const requests: string[] = []
  server.use(
    http.get(`${url}/tasks`, () =>
      HttpResponse.json([
        gdriveTask({
          backend: 'api',
          status: 'failed',
          upload_account_id: '3',
          remote_file_id: 'cloud-file',
          error_message: '配额不足',
        }),
      ]),
    ),
    http.post(`${url}/tasks/1/retry`, () => {
      requests.push('retry')
      return HttpResponse.json({ success: true })
    }),
  )
  await renderWithProviders(Upload, { initialState })
  expect(await screen.findByText('配额不足')).toBeVisible()
  expect(screen.getByText(/云端 ID：cloud-file/)).toBeVisible()
  await fireEvent.click(screen.getByRole('button', { name: '重试上传' }))
  await screen.findByText('上传任务已重新入队。')
  expect(requests).toEqual(['retry'])
})

it('普通管理用户可创建上传任务但不显示账号凭据管理', async () => {
  await renderWithProviders(Upload, { initialState: { user: { superUser: false } } })
  await screen.findByText('暂无直接上传任务。')
  expect(screen.queryByLabelText('总上传并发')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '设置 Token' })).not.toBeInTheDocument()
  expect(await screen.findByRole('button', { name: '开始上传' })).toBeVisible()
})
