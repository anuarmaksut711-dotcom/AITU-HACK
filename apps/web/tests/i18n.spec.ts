import { expect, test, type Page } from '@playwright/test'

const id = '00000000-0000-4000-8000-000000000001'
const chatId = '00000000-0000-4000-8000-000000000002'
const cardId = '00000000-0000-4000-8000-000000000003'
const timestamp = '2026-09-11T09:00:00Z'
const meeting = { id, title: 'Обсуждение бюджета — original', transcript: 'Нужно проверить бюджет. Budget unchanged. Бюджетті тексеру керек.', language: 'ru', status: 'transcribed', source_type: 'text', created_at: timestamp, updated_at: timestamp, transcript_length: 75, transcription: null, audio_filename: null, audio_bytes: null, segments: null }
const card = { id: cardId, kind: 'task', title: 'Проверить бюджет — original', description: '', assignee: null, due_date: '2026-09-15', due_text: null, priority: 'high', status: 'todo', reviewed: false, quote: null, quote_start: null, agreement: 'proposed', evidence: null, revisions: [], clarifications: ['assignee_missing'], origin: 'ai', start_char: null, end_char: null }
const board = { version: 1, status: 'ready', error_code: null, progress: 100, summary: [], cards: [card] }

async function mock(page: Page, authenticated = true) {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    const p = url.pathname
    if (p.endsWith('/auth/me')) return route.fulfill({ status: authenticated ? 200 : 401, json: authenticated ? { id, email: 'qa@example.test', display_name: 'QA' } : {} })
    if (p.endsWith('/auth/login')) return route.fulfill({ status: 401, json: {} })
    if (p.endsWith('/meetings')) return route.fulfill({ json: { items: [meeting], total: 24, offset: 0, limit: 20 } })
    if (p.endsWith(`/meetings/${id}`)) return route.fulfill({ json: meeting })
    if (p.endsWith('/board')) return route.fulfill({ json: board })
    if (p.endsWith('/assistant/conversations')) return route.fulfill({ json: [{ id: chatId, title: 'Новый чат', created_at: timestamp, updated_at: timestamp }] })
    if (p.endsWith(`/assistant/conversations/${chatId}`)) return route.fulfill({ json: { id: chatId, title: 'Новый чат', created_at: timestamp, updated_at: timestamp, turns: [] } })
    if (p.endsWith('/rag/config')) return route.fulfill({ json: { offline: true, llm_provider: 'local', llm_model: 'test', reasoning_effort: 'none', embedding_provider: 'local', embedding_model: 'test', embedding_dimensions: 3, cloud_configured: false } })
    if (p.endsWith('/live/rooms')) return route.fulfill({ json: [] })
    return route.fulfill({ status: 404, json: {} })
  })
}
async function language(page: Page, value: 'ru' | 'kk' | 'en') {
  await page.locator('.language-switcher input[role=combobox]').first().click()
  await page.getByRole('option', { name: { ru: 'Русский', kk: 'Қазақша', en: 'English' }[value], exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('lang', value)
}
async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1)
}

test('login switches all languages, preserves fields and updates existing validation', async ({ page }) => {
  await mock(page, false)
  await page.goto('/login')
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await expect(page.getByText('Введите корректную электронную почту', { exact: true })).toBeVisible()
  await language(page, 'en')
  await expect(page.getByRole('heading', { name: 'Sign in to workspace', exact: true })).toBeVisible()
  await expect(page.getByText('Enter a valid email address', { exact: true })).toBeVisible()
  await expect(page).toHaveTitle('Sign in · Soyle')
  await page.getByLabel('Email', { exact: true }).fill('draft@example.test')
  await page.getByLabel('Password', { exact: true }).fill('draft-password')
  await language(page, 'kk')
  await expect(page.getByLabel('Электрондық пошта', { exact: true })).toHaveValue('draft@example.test')
  await expect(page.getByLabel('Құпиясөз', { exact: true })).toHaveValue('draft-password')
  await expect(page.getByRole('heading', { name: 'Жұмыс кеңістігіне кіру', exact: true })).toBeVisible()
  await expect(page).toHaveTitle('Кіру · Soyle')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('lang', 'kk')
  await expect(page.getByRole('button', { name: 'Кіру', exact: true })).toBeVisible()
  await page.setViewportSize({ width: 375, height: 812 })
  await noOverflow(page)
})

test('browser locale, invalid preference and unavailable storage have safe defaults', async ({ page }) => {
  await mock(page, false)
  await page.addInitScript(() => {
    localStorage.setItem('soyle-locale', 'unsupported')
    Object.defineProperty(navigator, 'languages', { get: () => ['fr-FR', 'kk-KZ'] })
  })
  await page.goto('/login')
  await expect(page.locator('html')).toHaveAttribute('lang', 'kk')
  await language(page, 'en')
  await page.evaluate(() => { Storage.prototype.setItem = () => { throw new Error('blocked') } })
  await language(page, 'ru')
  await expect(page.getByRole('button', { name: 'Войти', exact: true })).toBeVisible()
})

test('cross-tab changes update the interface without resetting a draft', async ({ page, context }) => {
  await mock(page, false)
  await page.goto('/login')
  await page.getByLabel('Электронная почта', { exact: true }).fill('draft@example.test')
  const other = await context.newPage()
  await mock(other, false)
  await other.goto('/login')
  await language(other, 'en')
  await expect(page.getByLabel('Email', { exact: true })).toHaveValue('draft@example.test')
  await expect(page).toHaveTitle('Sign in · Soyle')
})

for (const locale of ['en', 'kk'] as const) {
  test(`meeting library, upload and Kanban localize in ${locale} without translating source data`, async ({ page }) => {
    await mock(page)
    await page.goto('/meetings')
    await language(page, locale)
    const english = locale === 'en'
    await expect(page.getByRole('heading', { name: english ? 'Meetings' : 'Кездесулер', exact: true })).toBeVisible()
    await expect(page.getByText(meeting.title, { exact: true })).toBeVisible()
    await expect(page.getByText(english ? 'Transcript ready' : 'Стенограмма дайын', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: english ? 'Upload audio' : 'Аудионы жүктеу', exact: true }).click()
    // The file picker does not open a dialog until a file is chosen.
    await page.locator('input[type=file]').setInputFiles({ name: 'test.wav', mimeType: 'audio/wav', buffer: Buffer.from('test') })
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByLabel(english ? 'Recording language' : 'Жазба тілі', { exact: true })).toBeVisible()
    await expect(dialog.getByRole('button', { name: english ? 'Close' : 'Жабу', exact: true })).toBeVisible()
    await dialog.getByRole('button', { name: english ? 'Close' : 'Жабу', exact: true }).click()
    await page.goto(`/meetings/${id}?view=kanban`)
    await expect(page.getByRole('heading', { name: meeting.title, exact: true }).first()).toBeVisible()
    await expect(page.getByText(card.title, { exact: true }).first()).toBeVisible()
    await expect(page.locator('.card-person').first()).toContainText(english ? 'No assignee' : 'Жауапты адам көрсетілмеген')
    await page.getByRole('button', { name: card.title, exact: true }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByLabel(english ? 'Due date' : 'Орындалу күні', { exact: true })).toBeVisible()
    await page.getByLabel(english ? 'Due date' : 'Орындалу күні', { exact: true }).click()
    await expect(page.getByRole('button', { name: english ? 'Next month' : 'Келесі ай', exact: true })).toBeVisible()
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: english ? 'Cancel' : 'Бас тарту', exact: true }).click()
    await page.goto(`/meetings/${id}?view=conversation`)
    await expect(page.getByText(meeting.transcript, { exact: true })).toBeVisible()
    await noOverflow(page)
  })
}

test('not-found page switches language and updates its title', async ({ page }) => {
  await mock(page, false)
  await page.goto('/missing-page')
  await language(page, 'en')
  await expect(page.getByRole('heading', { name: 'Page not found', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Back to meetings', exact: true })).toBeVisible()
  await expect(page).toHaveTitle('Page not found · Soyle')
})

test('chat suggestions and live landing update immediately', async ({ page }) => {
  await mock(page)
  await page.goto('/chat')
  await language(page, 'en')
  await expect(page.getByText('Which meeting discussed the budget?', { exact: true })).toBeVisible()
  await language(page, 'kk')
  await expect(page.getByText('Бюджет қай кездесуде талқыланды?', { exact: true })).toBeVisible()
  await page.goto('/live')
  await expect(page.getByRole('button', { name: 'Жаңа әңгіме', exact: true })).toBeVisible()
  await language(page, 'en')
  await expect(page.getByRole('button', { name: 'New conversation', exact: true })).toBeVisible()
  await page.setViewportSize({ width: 375, height: 812 })
  await noOverflow(page)
})

test('guest invitation localizes consent and keeps the participant name', async ({ page }) => {
  await mock(page, false)
  await page.route('**/live/rooms/*/invitation', (route) => route.fulfill({ json: { id, title: 'Комната — original', language: 'ru', status: 'active', created_at: timestamp, ended_at: null, meeting_id: null } }))
  await page.goto(`/live/${id}#invite=fixture-invitation`)
  await page.getByRole('textbox', { name: 'Как вас представить?', exact: true }).fill('Аружан')
  await language(page, 'en')
  await expect(page.getByRole('textbox', { name: 'What should we call you?', exact: true })).toHaveValue('Аружан')
  await expect(page.getByText('This conversation is recorded.', { exact: false })).toContainText('sent to OpenAI for analysis')
  await expect(page.getByRole('button', { name: 'Join conversation', exact: true })).toBeEnabled()
  await language(page, 'kk')
  await expect(page.getByRole('textbox', { name: 'Сізді қалай таныстырайық?', exact: true })).toHaveValue('Аружан')
  await expect(page.getByText('Әңгіме жазылады.', { exact: false })).toContainText('OpenAI-ға жіберіледі')
  await page.setViewportSize({ width: 375, height: 812 })
  await noOverflow(page)
})

test('open card follows another tab’s language without losing edits', async ({ page, context }) => {
  await mock(page)
  await page.goto(`/meetings/${id}?view=kanban`)
  await page.getByRole('button', { name: card.title, exact: true }).click()
  await page.getByRole('textbox', { name: 'Суть карточки', exact: true }).fill('Несохранённая карточка')
  const other = await context.newPage()
  await mock(other, false)
  await other.goto('/login')
  await language(other, 'en')
  await expect(page.getByRole('textbox', { name: 'Card title', exact: true })).toHaveValue('Несохранённая карточка')
  await expect(page.getByRole('combobox', { name: 'Card type', exact: true })).toHaveValue('Tasks')
  await expect(page.getByRole('dialog')).toHaveAccessibleName('Meeting card')
})

test('API errors use the selected language and never show internal exception text', async ({ page }) => {
  await mock(page)
  await page.route('**/api/v1/meetings?*', (route) => route.fulfill({ status: 403, json: { detail: 'INTERNAL_DATABASE_SECRET' } }))
  await page.goto('/meetings')
  await language(page, 'en')
  await expect(page.getByText('You do not have permission for this action.', { exact: true })).toBeVisible()
  await language(page, 'kk')
  await expect(page.getByText('Бұл әрекетке рұқсатыңыз жеткіліксіз.', { exact: true })).toBeVisible()
  await expect(page.getByText('INTERNAL_DATABASE_SECRET')).toHaveCount(0)
})

for (const width of [1440, 375]) {
  test(`only chat body scrolls at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await mock(page)
    await page.route(`**/assistant/conversations/${chatId}`, (route) => route.fulfill({ json: {
      id: chatId, title: 'Scroll test', created_at: timestamp, updated_at: timestamp,
      turns: Array.from({ length: 20 }, (_, index) => ({
        id: `00000000-0000-4000-8000-${String(index + 100).padStart(12, '0')}`,
        question: `Question ${index}`, created_at: timestamp,
        result: { mode: 'assistant', answer: 'Long answer with several paragraphs.\n'.repeat(12) },
      })),
    } }))
    await page.goto('/chat')
    const body = page.locator('.workspace-chat-body')
    await expect(page.getByText('Question 19', { exact: true })).toBeVisible()
    const header = page.locator('.workspace-chat-header')
    const composer = page.locator('.workspace-chat-composer-wrap')
    const headerBefore = await header.boundingBox()
    const composerBefore = await composer.boundingBox()
    expect(composerBefore!.y + composerBefore!.height).toBeLessThanOrEqual(900)
    await body.evaluate((element) => { element.scrollTop = 0 })
    await body.hover()
    await page.mouse.wheel(0, 500)
    await expect.poll(() => body.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)
    expect(await page.evaluate(() => window.scrollY)).toBe(0)
    expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThanOrEqual(901)
    expect(await header.boundingBox()).toEqual(headerBefore)
    expect(await composer.boundingBox()).toEqual(composerBefore)
    await noOverflow(page)
  })
}

for (const viewport of [1440, 375]) {
test(`board citations open resizable Kanban at ${viewport}px without leaving chat`, async ({ page }, testInfo) => {
  await page.setViewportSize({ width: viewport, height: 900 })
  await mock(page)
  await page.route(`**/assistant/conversations/${chatId}`, (route) => route.fulfill({ json: {
    id: chatId, title: 'Board sources', created_at: timestamp, updated_at: timestamp,
    turns: [{ id: cardId, question: 'Show tasks', created_at: timestamp, result: {
      mode: 'meetings', status: 'answered', answer: 'Two sources.', sources: [],
      claims: [{ text: 'Two supporting records from the same meeting.', citations: [
        { kind: 'board', source_id: 'board-1', quote: 'First supporting quote.' },
        { kind: 'board', source_id: 'board-2', quote: 'Second supporting quote.' },
      ] }],
      coverage: { total: 1, ready: 1, pending: 0, failed: 0, not_indexed: 0, unavailable: 0 },
      board_sources: ['board-1', 'board-2'].map((source_id) => ({ source_id, kind: 'kanban', meeting_id: id, meeting_title: meeting.title, card_id: cardId, title: card.title, text: 'Source text', board_version: 1, provisional: false, transcript_current: true, transcript_quote: null })),
      board_coverage: { available_sources: 2, selected_sources: 2 },
    } }],
  } }))
  await page.goto('/chat')
  await page.getByRole('textbox', { name: 'Сообщение ассистенту' }).fill('Сохранить черновик')
  const source = page.locator('.workspace-chat-board-source')
  await expect(source).toHaveCount(1)
  await source.click()
  const panel = page.getByRole('dialog')
  await expect(panel).toBeVisible()
  if (viewport > 760) {
    const handle = panel.getByRole('separator', { name: 'Ширина панели' })
    const before = await panel.boundingBox()
    const box = await handle.boundingBox()
    await page.mouse.move(box!.x + 4, box!.y + box!.height / 2)
    await page.mouse.down()
    await page.mouse.move(box!.x + 164, box!.y + box!.height / 2, { steps: 8 })
    await page.mouse.up()
    await expect.poll(async () => (await panel.boundingBox())!.width).toBeLessThan(before!.width - 140)
    await handle.focus()
    await page.keyboard.press('Home')
    await expect(handle).toHaveAttribute('aria-valuenow', '480')
    await page.keyboard.press('ArrowLeft')
    await expect(handle).toHaveAttribute('aria-valuenow', '520')
    await page.keyboard.press('End')
  } else {
    await expect(panel.getByRole('separator')).toHaveCount(0)
    expect((await panel.boundingBox())!.width).toBe(375)
  }
  await expect(panel.getByRole('button', { name: card.title, exact: true })).toBeVisible()
  await expect(page).toHaveURL(/\/chat$/)
  await page.screenshot({ path: testInfo.outputPath('kanban-panel.png') })
  expect(await panel.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
  await panel.getByRole('button', { name: /Цитаты из ответа/ }).click()
  await expect(panel.getByText('First supporting quote.', { exact: true })).toBeVisible()
  await expect(panel.getByText('Second supporting quote.', { exact: true })).toBeVisible()
  await panel.getByRole('button', { name: card.title, exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Суть карточки', exact: true })).toHaveValue(card.title)
  await page.getByRole('button', { name: 'Закрыть карточку', exact: true }).click()
  await page.getByRole('button', { name: 'Закрыть панель встречи', exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Сообщение ассистенту' })).toHaveValue('Сохранить черновик')
})

}

for (const choice of ['open', 'none', 'foreign', 'reply', 'navigation', 'choose'] as const) {
  test(`AI presentation choice ${choice} controls automatic panel opening`, async ({ page }) => {
    await mock(page)
    let turns: Array<Record<string, unknown>> = []
    await page.route(`**/assistant/conversations/${chatId}`, (route) => route.fulfill({ json: {
      id: chatId, title: 'AI panel', created_at: timestamp, updated_at: timestamp, turns,
    } }))
    await page.route(`**/assistant/conversations/${chatId}/turns/pending`, (route) => {
      const data = route.request().postDataJSON()
      const turn = { id: data.id, question: data.question, created_at: timestamp, result: { mode: data.state, answer: '', activity: data.activity } }
      turns = [turn]
      return route.fulfill({ json: turn })
    })
    await page.route(`**/assistant/conversations/${chatId}/turns`, (route) => {
      const data = route.request().postDataJSON()
      const turn = { ...data, created_at: timestamp }
      turns = [turn]
      return route.fulfill({ json: turn })
    })
    const coverage = { total: 1, ready: 1, pending: 0, failed: 0, not_indexed: 0, unavailable: 0 }
    await page.route('**/rag/index', (route) => route.fulfill({ json: coverage }))
    await page.route('**/assistant/chat/stream', (route) => route.fulfill({ json: {
      action: choice === 'reply' ? 'reply' : ['navigation', 'choose'].includes(choice) ? 'open_panel' : 'search_meetings',
      answer: choice === 'reply' ? 'A plain conversational answer.' : '', search_query: 'Show the kanban',
    } }))
    await page.route('**/assistant/panel', (route) => route.fulfill({ json: {
      mode: 'navigation', answer: 'Choose or open the meeting.', view: 'kanban',
      panel: choice === 'navigation' ? { meeting_id: id, view: 'kanban' } : null,
      meetings: [{ id, title: meeting.title }],
    } }))
    if (['navigation', 'choose'].includes(choice)) {
      await page.route('**/rag/index', () => { throw new Error('Navigation must not request a search index') })
      await page.route('**/rag/chat/stream', () => { throw new Error('Navigation must not search transcripts') })
    }
    await page.route('**/rag/chat/stream', (route) => route.fulfill({ json: {
      status: 'answered', answer: 'The board contains a task.', sources: [], coverage,
      panel: choice === 'none' ? null : { meeting_id: choice === 'foreign' ? cardId : id, view: 'kanban' },
      claims: [{ text: 'The board contains a task.', citations: [{ kind: 'board', source_id: 'B1', quote: 'Task quote.' }] }],
      board_sources: [{ source_id: 'B1', kind: 'kanban', meeting_id: id, meeting_title: meeting.title, card_id: cardId,
        title: card.title, text: 'Task quote.', board_version: 1, provisional: false, transcript_current: true, transcript_quote: null }],
      board_coverage: { available_sources: 1, selected_sources: 1 },
    } }))
    await page.goto('/chat')
    const input = page.getByRole('textbox', { name: 'Сообщение ассистенту' })
    await input.fill('Покажи канбан')
    await page.getByRole('button', { name: 'Отправить вопрос', exact: true }).click()
    await expect(page.getByText(choice === 'reply' ? 'A plain conversational answer.' : ['navigation', 'choose'].includes(choice) ? 'Choose or open the meeting.' : 'The board contains a task.', { exact: true }).first()).toBeVisible()
    if (choice === 'choose') {
      await expect(page.getByRole('dialog')).toHaveCount(0)
      await page.locator('.workspace-chat-board-source').click()
    }
    if (['open', 'navigation', 'choose'].includes(choice)) {
      await expect(page.getByRole('dialog')).toBeVisible()
      await expect(page.getByRole('dialog').getByRole('button', { name: card.title, exact: true })).toBeVisible()
      await page.getByRole('button', { name: 'Закрыть панель встречи', exact: true }).click()
      await expect(page.getByRole('dialog')).toHaveCount(0)
    } else {
      await expect(page.getByRole('dialog')).toHaveCount(0)
    }
    await page.reload()
    await expect(page.getByText(choice === 'reply' ? 'A plain conversational answer.' : ['navigation', 'choose'].includes(choice) ? 'Choose or open the meeting.' : 'The board contains a task.', { exact: true }).first()).toBeVisible()
    await expect(page.getByRole('dialog')).toHaveCount(0)
  })
}
