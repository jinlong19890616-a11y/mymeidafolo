import GdrivePage from '@/pages/gdrive.vue'
import GdriveCreateDialog from '@/views/gdrive/GdriveCreateDialog.vue'
import api from '@/api'
import { gdriveBytes, gdriveTaskTitle, isGdriveTaskActive } from '@/api/gdrive'
import { useUserStore } from '@/stores'
import { DEFAULT_PERMISSIONS } from '@/utils/permission'
import { renderWithProviders } from '@tests/support/render'
import { server } from '@tests/support/msw/server'
import { gdriveHistory, gdrivePreflight, gdriveSettings, gdriveTask } from '@tests/support/fixtures/gdrive'
import { fireEvent, screen, waitFor, within } from '@testing-library/vue'
import { HttpResponse, http } from 'msw'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const url = '*/gdrive'
const initialState = { user: { superUser: false, permissions: { ...DEFAULT_PERMISSIONS, manage: true } } }

async function openCreate() {
  const rendered = await renderWithProviders(GdriveCreateDialog)
  await screen.findByText(/当前策略：归档及 STRM 成功后删除源文件/)
  await fireEvent.update(screen.getByLabelText('源文件路径'), ' /downloads/Example.mkv ')
  return rendered
}

async function runPreflight() {
  await fireEvent.click(screen.getByRole('button', { name: '只读预检' }))
  await screen.findByText('预检通过，可以创建任务')
}

async function renderFilePage() {
  const rendered = await renderWithProviders(GdrivePage, { initialState })
  await fireEvent.click(screen.getByRole('tab', { name: '文件任务' }))
  return rendered
}

beforeEach(() => {
  server.use(
    http.get(`${url}/tasks`, () => HttpResponse.json([gdriveTask()])),
    http.get(`${url}/settings`, () => HttpResponse.json(gdriveSettings)),
    http.post(`${url}/preflight`, () => HttpResponse.json(gdrivePreflight)),
    http.post(`${url}/tasks`, () => HttpResponse.json({ success: true, data: { id: 2 } })),
    http.get(`${url}/tasks/1/history`, () => HttpResponse.json(gdriveHistory)),
    http.get(`${url}/directories`, () => HttpResponse.json([])),
    http.get(`${url}/scans`, () => HttpResponse.json([])),
  )
})

describe('GDrive 任务主流程（离线 HTTP 边界）', () => {
  it('展示任务、搜索和清空、状态筛选以及详情历史', async () => {
    server.use(
      http.get(`${url}/tasks`, () =>
        HttpResponse.json([
          gdriveTask(),
          gdriveTask({ id: 2, title: '另一部电影', status: 'failed', error_message: '磁盘不可写' }),
        ]),
      ),
    )
    await renderFilePage()
    await screen.findByText('示例电影')
    const search = screen.getByLabelText('搜索已加载任务的标题或路径')
    await fireEvent.update(search, 'missing')
    expect(await screen.findByText('没有匹配的任务。')).toBeVisible()
    await fireEvent.update(search, '')
    expect(await screen.findByText('另一部电影')).toBeVisible()
    await fireEvent.mouseDown(screen.getByLabelText('任务状态'))
    await fireEvent.click(await screen.findByRole('option', { name: '失败' }))
    await waitFor(() => expect(screen.queryByText('示例电影')).not.toBeInTheDocument())
    await fireEvent.mouseDown(screen.getByLabelText('任务状态'))
    await fireEvent.click(await screen.findByRole('option', { name: '全部' }))
    await fireEvent.click(screen.getByRole('button', { name: '任务详情 #1' }))
    const dialog = await screen.findByRole('dialog', { name: '任务详情' })
    expect(await within(dialog).findByText('开始复制源文件')).toBeVisible()
    expect(within(dialog).getByText('/archive/Example.strm')).toBeVisible()
    server.use(http.get(`${url}/tasks/1/history`, () => HttpResponse.json({}, { status: 500 })))
    await fireEvent.click(within(dialog).getByRole('button', { name: '刷新历史' }))
    expect(await within(dialog).findByText(/历史加载失败/)).toBeVisible()
    await fireEvent.click(within(dialog).getByRole('button', { name: '关闭' }))
  })

  it('首次加载失败可手动恢复，创建后刷新列表', async () => {
    server.use(http.get(`${url}/tasks`, () => HttpResponse.json({}, { status: 500 })))
    await renderFilePage()
    expect(await screen.findByText(/任务加载失败/)).toBeVisible()
    server.use(http.get(`${url}/tasks`, () => HttpResponse.json([])))
    await fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await screen.findByText(/暂无归档任务/)
    await fireEvent.click(screen.getByRole('button', { name: '新建任务' }))
    await screen.findByText(/当前策略/)
    await fireEvent.update(screen.getByLabelText('源文件路径'), '/downloads/Example.mkv')
    await runPreflight()
    server.use(http.get(`${url}/tasks`, () => HttpResponse.json([gdriveTask({ id: 2, title: '新任务' })])))
    await fireEvent.click(screen.getByRole('button', { name: '创建并启动' }))
    expect(await screen.findByText('任务已创建并启动。')).toBeVisible()
    expect(await screen.findByText('新任务')).toBeVisible()
  })

  it('无管理权限不发请求，撤销权限关闭对话框并清理列表', async () => {
    const list = vi.fn(() => HttpResponse.json([gdriveTask()]))
    server.use(http.get(`${url}/tasks`, list))
    const rendered = await renderWithProviders(GdrivePage, {
      initialState: { user: { superUser: false, permissions: { ...DEFAULT_PERMISSIONS, manage: false } } },
    })
    expect(screen.getByText(/需要管理权限/)).toBeVisible()
    expect(list).not.toHaveBeenCalled()
    useUserStore(rendered.pinia).permissions.manage = true
    await nextTick()
    await fireEvent.click(screen.getByRole('tab', { name: '文件任务' }))
    await screen.findByText('示例电影')
    await fireEvent.click(screen.getByRole('button', { name: '新建任务' }))
    await screen.findByRole('dialog', { name: '新建任务' })
    useUserStore(rendered.pinia).permissions.manage = false
    await nextTick()
    expect(screen.queryByText('示例电影')).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('只在可见且存在活跃任务时轮询，卸载停止轮询', async () => {
    let calls = 0
    server.use(
      http.get(`${url}/tasks`, () => {
        calls++
        return HttpResponse.json([gdriveTask()])
      }),
    )
    const rendered = await renderFilePage()
    await screen.findByText('示例电影')
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await vi.waitFor(() => expect(calls).toBe(2))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: '刷新' })).not.toHaveClass('v-btn--loading'))
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(10000)
    expect(calls).toBe(2)
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.waitFor(() => expect(calls).toBe(3))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: '刷新' })).not.toHaveClass('v-btn--loading'))
    await vi.advanceTimersByTimeAsync(5000)
    await vi.waitFor(() => expect(calls).toBe(4))
    rendered.unmount()
    await vi.advanceTimersByTimeAsync(15000)
    expect(calls).toBe(4)
  })

  it('预检通过前不能创建，修改路径清除结果，继承策略省略字段', async () => {
    const rendered = await openCreate()
    const requests: unknown[] = []
    server.use(
      http.post(`${url}/tasks`, async ({ request }) => {
        requests.push(await request.json())
        return HttpResponse.json({ success: true, data: { id: 7 } })
      }),
    )
    expect(screen.getByRole('button', { name: '创建并启动' })).toBeDisabled()
    await runPreflight()
    await fireEvent.update(screen.getByLabelText('自定义归档路径（可选）'), '/archive/custom.mkv')
    await waitFor(() => expect(screen.queryByText('预检通过，可以创建任务')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: '创建并启动' })).toBeDisabled()
    await runPreflight()
    await fireEvent.click(screen.getByRole('button', { name: '创建并启动' }))
    await waitFor(() => expect(rendered.emitted().created).toEqual([[7]]))
    expect(requests).toEqual([{ source_path: '/downloads/Example.mkv', archive_path: '/archive/custom.mkv' }])
  })

  it('新建成功时等待进行中的旧列表完成，再查询最新列表', async () => {
    await renderFilePage()
    await screen.findByText('示例电影')
    await fireEvent.click(screen.getByRole('button', { name: '新建任务' }))
    await screen.findByText(/当前策略/)
    await fireEvent.update(screen.getByLabelText('源文件路径'), '/downloads/Example.mkv')
    await runPreflight()
    let release!: () => void
    let started = false
    const pending = new Promise<void>(resolve => {
      release = resolve
    })
    server.use(
      http.get(`${url}/tasks`, async () => {
        started = true
        await pending
        return HttpResponse.json([gdriveTask()])
      }),
    )
    document.dispatchEvent(new Event('visibilitychange'))
    await waitFor(() => expect(started).toBe(true))
    await fireEvent.click(screen.getByRole('button', { name: '创建并启动' }))
    await screen.findByText('任务已创建并启动。')
    server.use(http.get(`${url}/tasks`, () => HttpResponse.json([gdriveTask({ id: 2, title: '排队刷新后的任务' })])))
    release()
    expect(await screen.findByText('排队刷新后的任务')).toBeVisible()
  })

  it('全部任务完成后停止轮询，卸载中止尚未完成的列表请求', async () => {
    server.use(http.get(`${url}/tasks`, () => HttpResponse.json([gdriveTask({ status: 'completed' })])))
    const list = vi.fn()
    const rendered = await renderFilePage()
    await screen.findByText('示例电影')
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    server.use(
      http.get(`${url}/tasks`, () => {
        list()
        return HttpResponse.json([])
      }),
    )
    await vi.advanceTimersByTimeAsync(15000)
    expect(list).not.toHaveBeenCalled()
    vi.useRealTimers()
    let release!: () => void
    const pending = new Promise<void>(resolve => {
      release = resolve
    })
    let signal: { readonly aborted: boolean } | undefined
    const interceptor = api.interceptors.request.use(config => {
      if (config.url === 'gdrive/tasks') signal = config.signal
      return config
    })
    server.use(
      http.get(`${url}/tasks`, async () => {
        await pending
        return HttpResponse.json([])
      }),
    )
    await fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => expect(signal).toBeDefined())
    rendered.unmount()
    expect(signal?.aborted).toBe(true)
    api.interceptors.request.eject(interceptor)
    release()
  })

  it.each([
    ['保留源文件', false],
    ['成功后删除源文件', true],
  ] as const)('显式策略 %s 传递 boolean，业务失败不关闭窗口', async (label, expected) => {
    await openCreate()
    await fireEvent.mouseDown(screen.getByLabelText('完成后的源文件策略'))
    await fireEvent.click(await screen.findByRole('option', { name: label }))
    const requests: unknown[] = []
    server.use(
      http.post(`${url}/tasks`, async ({ request }) => {
        requests.push(await request.json())
        return HttpResponse.json({ success: false, message: '任务冲突，请检查源路径' })
      }),
    )
    await runPreflight()
    await fireEvent.click(screen.getByRole('button', { name: '创建并启动' }))
    expect(await screen.findByText('任务冲突，请检查源路径')).toBeVisible()
    expect(requests).toEqual([{ source_path: '/downloads/Example.mkv', remove_source: expected }])
  })

  it('预检失败展示原因且不能创建，配置禁用时阻止预检', async () => {
    server.use(
      http.post(`${url}/preflight`, () =>
        HttpResponse.json({
          ...gdrivePreflight,
          ready: false,
          checks: [{ name: 'source', passed: false, level: 'error', message: '源文件不存在' }],
        }),
      ),
    )
    const rendered = await openCreate()
    await fireEvent.click(screen.getByRole('button', { name: '只读预检' }))
    expect(await screen.findByText('源文件不存在')).toBeVisible()
    expect(screen.getByRole('button', { name: '创建并启动' })).toBeDisabled()
    rendered.unmount()
    server.use(http.get(`${url}/settings`, () => HttpResponse.json({ ...gdriveSettings, enabled: false })))
    await renderWithProviders(GdriveCreateDialog)
    await screen.findByText(/后处理尚未启用/)
    expect(screen.getByRole('button', { name: '只读预检' })).toBeDisabled()
  })

  it('配置读取失败支持重试，预检请求失败可恢复', async () => {
    server.use(http.get(`${url}/settings`, () => HttpResponse.json({}, { status: 500 })))
    await renderWithProviders(GdriveCreateDialog)
    await screen.findByText(/无法读取配置/)
    server.use(http.get(`${url}/settings`, () => HttpResponse.json({ ...gdriveSettings, remove_source: false })))
    await fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await screen.findByText(/成功后保留源文件/)
    await fireEvent.update(screen.getByLabelText('源文件路径'), '/downloads/Example.mkv')
    server.use(http.post(`${url}/preflight`, () => HttpResponse.json({}, { status: 500 })))
    await fireEvent.click(screen.getByRole('button', { name: '只读预检' }))
    expect(await screen.findByText('预检请求失败，请重试。')).toBeVisible()
    server.use(http.post(`${url}/preflight`, () => HttpResponse.json(gdrivePreflight)))
    await runPreflight()
  })

  it('预检请求未完成时修改路径不接受旧响应', async () => {
    let resolve!: () => void
    const pending = new Promise<void>(done => {
      resolve = done
    })
    let started = false
    server.use(
      http.post(`${url}/preflight`, async () => {
        started = true
        await pending
        return HttpResponse.json(gdrivePreflight)
      }),
    )
    await openCreate()
    await fireEvent.click(screen.getByRole('button', { name: '只读预检' }))
    await waitFor(() => expect(started).toBe(true))
    await fireEvent.update(screen.getByLabelText('源文件路径'), '/downloads/Changed.mkv')
    resolve()
    await nextTick()
    expect(screen.getByRole('button', { name: '创建并启动' })).toBeDisabled()
    expect(screen.queryByText('预检通过，可以创建任务')).not.toBeInTheDocument()
  })

  it('提交期间防止重复提交，结果不明后要求先查询列表', async () => {
    let resolve!: () => void
    const pending = new Promise<void>(done => {
      resolve = done
    })
    let calls = 0
    server.use(
      http.post(`${url}/tasks`, async () => {
        calls++
        await pending
        return HttpResponse.error()
      }),
    )
    await openCreate()
    await runPreflight()
    const button = screen.getByRole('button', { name: '创建并启动' })
    await fireEvent.click(button)
    await fireEvent.click(button)
    await waitFor(() => expect(calls).toBe(1))
    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled()
    resolve()
    expect(await screen.findByText(/未收到创建结果/)).toBeVisible()
    expect(button).toBeDisabled()
    await fireEvent.update(screen.getByLabelText('源文件路径'), '/downloads/Other.mkv')
    expect(screen.getByText(/未收到创建结果/)).toBeVisible()
    expect(calls).toBe(1)
  })

  it('字节、标题及未知状态展示兼容后端新增状态', () => {
    expect(gdriveBytes(0)).toBe('0 B')
    expect(gdriveBytes(1024)).toBe('1.0 KiB')
    expect(gdriveBytes(Number.NaN)).toBe('0 B')
    expect(gdriveTaskTitle(gdriveTask({ title: null }))).toBe('Example.mkv')
    expect(isGdriveTaskActive(gdriveTask({ status: 'new_stage' }))).toBe(true)
    expect(isGdriveTaskActive(gdriveTask({ status: 'completed' }))).toBe(false)
  })

  it('取消新建返回列表，详情支持空历史与未知状态', async () => {
    server.use(
      http.get(`${url}/tasks`, () =>
        HttpResponse.json([gdriveTask({ status: 'future_status', stage: 'future_stage', remove_source: false })]),
      ),
      http.get(`${url}/tasks/1/history`, () => HttpResponse.json([])),
    )
    await renderFilePage()
    await screen.findByText('示例电影')
    await fireEvent.click(screen.getByRole('button', { name: '新建任务' }))
    await screen.findByRole('dialog', { name: '新建任务' })
    await fireEvent.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await fireEvent.click(screen.getByRole('button', { name: '任务详情 #1' }))
    const detail = await screen.findByRole('dialog', { name: '任务详情' })
    expect(await within(detail).findByText('暂无状态历史。')).toBeVisible()
    expect(within(detail).getByText('future_status')).toBeVisible()
    expect(within(detail).getByText('保留源文件')).toBeVisible()
  })
})
