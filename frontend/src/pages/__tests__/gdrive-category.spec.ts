import Rules from '@/views/gdrive/GdriveRules.vue'
import RuleDialog from '@/views/gdrive/GdriveRuleDialog.vue'
import Editor from '@/views/gdrive/GdriveCategoryEditor.vue'
import { renderWithProviders } from '@tests/support/render'
import { server } from '@tests/support/msw/server'
import { fireEvent, screen, waitFor, within } from '@testing-library/vue'
import { HttpResponse, http } from 'msw'
import { beforeEach, expect, it } from 'vitest'
import type { GdriveRuleConfig } from '@/api/gdrive'

const url = '*/gdrive'
const policy = {
  movie: [
    { name: '电影/动画', conditions: { genre_ids: '16', '?original_language': 'ja', series_keywords: '哆啦A梦' } },
  ],
  tv: [{ name: '剧集/其他', conditions: {} }],
  path_mode: 'relative' as const,
  unmatched: 'skip' as const,
  fallback: '未分类',
}
beforeEach(() => {
  server.use(
    http.get(`${url}/rules`, () => HttpResponse.json([])),
    http.post(`${url}/rules/preview`, () =>
      HttpResponse.json({ success: true, data: { path: '电影/动画/示例 (2024)/示例.mkv' } }),
    ),
  )
})

it('归档规则页提供独立二次分类入口并可导入后保存可复用规则', async () => {
  let saved: GdriveRuleConfig | undefined
  let imported: unknown
  server.use(
    http.post(`${url}/categories/import`, async ({ request }) => {
      imported = await request.json()
      return HttpResponse.json({ success: true, data: { policy, warnings: ['只使用已有本地信息'] } })
    }),
    http.post(`${url}/rules`, async ({ request }) => {
      saved = (await request.json()) as GdriveRuleConfig
      return HttpResponse.json({ success: true })
    }),
  )
  await renderWithProviders(Rules, { initialState: { user: { superUser: true } } })
  await fireEvent.click(await screen.findByRole('button', { name: '新增二次分类规则' }))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByLabelText('启用自定义二次分类规则')).toBeChecked()
  await fireEvent.update(within(dialog).getByLabelText('规则名称'), 'Symedia 分类')
  await fireEvent.click(within(dialog).getByRole('button', { name: '导入／导出 YAML 分类规则' }))
  await fireEvent.update(within(dialog).getByLabelText('分类 YAML 内容'), 'movie:\n  电影/动画:\n    genre_ids: "16"')
  await fireEvent.click(within(dialog).getByRole('button', { name: '校验并应用到草稿' }))
  expect(await within(dialog).findByText('只使用已有本地信息')).toBeVisible()
  expect(imported).toEqual(expect.objectContaining({ policy: expect.objectContaining({ path_mode: 'relative' }) }))
  expect(within(dialog).getByLabelText('分类名称')).toHaveValue('电影/动画')
  expect(
    within(dialog)
      .getAllByLabelText('可选条件（?，组内任一命中）')
      .some(input => (input as HTMLInputElement).checked),
  ).toBe(true)
  await fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))
  await waitFor(() => expect(saved?.category_policy).toEqual(policy))
})

it('无效导入保留当前草稿，YAML 导出保留可选条件和分类顺序', async () => {
  server.use(
    http.post(`${url}/categories/import`, () => HttpResponse.json({ success: false, message: '分类 YAML 格式无效' })),
  )
  await renderWithProviders(Editor, { props: { modelValue: structuredClone(policy) } })
  await fireEvent.click(screen.getByRole('button', { name: '导入／导出 YAML 分类规则' }))
  await fireEvent.update(screen.getByLabelText('分类 YAML 内容'), 'invalid')
  await fireEvent.click(screen.getByRole('button', { name: '校验并应用到草稿' }))
  expect(await screen.findByText('分类 YAML 格式无效')).toBeVisible()
  expect(screen.getByLabelText('分类名称')).toHaveValue('电影/动画')
  await fireEvent.click(screen.getByRole('button', { name: '将当前规则填入 YAML' }))
  const yaml = (screen.getByLabelText('分类 YAML 内容') as HTMLTextAreaElement).value
  expect(yaml).toContain('"?original_language": "ja"')
  expect(yaml).toContain('"剧集/其他": null')
})

it('已有规则编辑保留系列字段和目录模式，开关不会丢失草稿', async () => {
  let saved: GdriveRuleConfig | undefined
  server.use(
    http.put(`${url}/rules/1`, async ({ request }) => {
      saved = (await request.json()) as GdriveRuleConfig
      return HttpResponse.json({ success: true })
    }),
  )
  await renderWithProviders(RuleDialog, {
    props: {
      rule: {
        id: 1,
        revision: 1,
        updated_at: '2026',
        name: '已有分类',
        media_type: 'auto',
        categories: ['media_type'],
        movie_directory: '{{ title }} ({{ year }})',
        movie_filename: '{{ title }}',
        tv_directory: '{{ title }}/Season {{ season }}',
        tv_filename: '{{ season_episode }}',
        category_policy: structuredClone(policy),
      },
    },
  })
  const toggle = screen.getByLabelText('启用自定义二次分类规则')
  await fireEvent.click(toggle)
  await fireEvent.click(toggle)
  expect(screen.getByLabelText('标题系列关键词')).toHaveValue('哆啦A梦')
  await fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(saved?.category_policy).toEqual(policy))
})
