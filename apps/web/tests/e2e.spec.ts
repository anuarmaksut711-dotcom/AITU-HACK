import { randomUUID } from 'node:crypto';
import { expect, test, type Page, type Route } from '@playwright/test';

test.use({ locale: 'ru-RU' });

function conversationStore() {
  const chats = new Map<string, { id: string; title: string; created_at: string; updated_at: string; turns: Array<Record<string, unknown>> }>();
  return async (route: Route) => {
    const parts = new URL(route.request().url()).pathname.split('/').filter(Boolean);
    const id = parts[4];
    if (!id && route.request().method() === 'GET') return route.fulfill({ json: [...chats.values()] });
    if (!id) {
      const value = route.request().postDataJSON();
      const chat = { id: value.id, title: 'Новый чат', created_at: new Date().toISOString(), updated_at: new Date().toISOString(), turns: [] };
      chats.set(chat.id, chat);
      return route.fulfill({ json: chat });
    }
    const chat = chats.get(id)!;
    if (parts[5] === 'turns') {
      const data = route.request().postDataJSON();
      const existing = chat.turns.find((t) => t.id === data.id);
      const oldResult = existing?.result as { mode: string; activity?: string } | undefined;
      if (existing && oldResult?.mode !== 'pending' && oldResult?.mode !== 'failed') return route.fulfill({ json: existing });
      const result = parts[6] === 'pending'
        ? { mode: data.state, answer: '', activity: oldResult?.activity === 'creating' ? 'creating' : data.activity, error_code: data.error_code ?? null, error_status: data.error_status ?? null }
        : data.result;
      const saved = { id: data.id, question: data.question, result, created_at: existing?.created_at ?? new Date().toISOString() };
      if (existing) chat.turns = chat.turns.map((item) => item.id === saved.id ? saved : item);
      else chat.turns.push(saved);
      if (chat.title === 'Новый чат') chat.title = data.question;
      return route.fulfill({ json: saved });
    }
    return route.fulfill({ json: chat });
  };
}

async function expectNoHorizontalOverflow(page: Page) {
  await expect
    .poll(
      () =>
        page.evaluate(
          () =>
            Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) -
            window.innerWidth,
        ),
      { message: 'The page must fit the viewport without horizontal scrolling.' },
    )
    .toBeLessThanOrEqual(1);
}

async function expectTranscript(page: Page, transcript: string) {
  const content = page.getByTestId('transcript');
  await expect(content).toBeVisible();
  // toHaveText normalizes whitespace; the stored transcript must remain verbatim.
  await expect.poll(() => content.textContent()).toBe(transcript);
}

async function expectNonceScrollLock(page: Page) {
  await expect(page.locator('body')).toHaveCSS('overflow', 'hidden');
  await expect.poll(() => page.evaluate(() => {
    const nonce = document.querySelector<HTMLMetaElement>('meta[name="csp-nonce"]')?.content;
    const styles = Array.from(document.querySelectorAll('style'))
      .filter((style) => style.textContent?.includes('data-scroll-locked'));
    return Boolean(nonce && nonce !== '__AIMEET_CSP_NONCE__' && styles.length > 0 &&
      styles.every((style) => style.nonce === nonce && style.sheet !== null));
  })).toBe(true);
}

test('authenticated meeting lifecycle persists text and works on mobile', async ({ page }, testInfo) => {
  const browserErrors: string[] = [];
  page.on('pageerror', (error) => browserErrors.push(error.message));
  page.on('console', (message) => {
    if (/Content Security Policy|violates.*policy/i.test(message.text())) browserErrors.push(message.text());
  });
  const email = process.env.E2E_EMAIL;
  const password = process.env.E2E_PASSWORD;
  if (!email || !password) {
    throw new Error('Set E2E_EMAIL and E2E_PASSWORD for a provisioned test account.');
  }

  const title = `E2E сохранение ${randomUUID()}`;
  const transcript =
    '\n  Алия: Проверяем сохранение текста встречи без изменения пробелов.\n\n' +
    'Данияр: Подтверждаю, стенограмма должна сохраниться после перезагрузки.  \n';
  let createdPath: string | undefined;
  let deleted = false;

  // One login for the complete smoke keeps the real login rate limit intact.
  await page.goto('/login');
  await expect(
    page.getByRole('heading', { name: 'Войти в рабочее пространство', exact: true }),
  ).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page.getByLabel('Электронная почта', { exact: true })).toHaveAttribute('aria-invalid', 'true');
  await expect(page.getByLabel('Электронная почта', { exact: true })).toBeFocused();
  await page.getByLabel('Электронная почта', { exact: true }).fill(email);
  await page.getByLabel('Пароль', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page).toHaveURL(/\/meetings(?:\?.*)?$/);

  try {
    const main = page.getByRole('main');
    await expect(main.getByRole('heading', { name: 'Встречи', exact: true })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    const chooser = page.waitForEvent('filechooser');
    await main.getByRole('button', { name: 'Загрузить аудио', exact: true }).first().click();
    await (await chooser).setFiles({ name: 'Проверка выбора.wav', mimeType: 'audio/wav', buffer: Buffer.from('UI file chooser check') });
    const uploadDialog = page.getByRole('dialog', { name: 'Загрузить аудио', exact: true });
    await expect(uploadDialog).toBeVisible();
    await expect(uploadDialog.getByLabel('Название встречи', { exact: true })).toHaveValue('Проверка выбора');
    await page.setViewportSize({ width: 320, height: 740 });
    const language = uploadDialog.getByRole('combobox', { name: 'Язык записи', exact: true });
    await language.click();
    await page.getByRole('option', { name: 'Русский', exact: true }).click();
    await expect(language).toHaveValue('Русский');
    await expectNoHorizontalOverflow(page);
    // A failed upload keeps the selected source and settings; no fixture audio is stored.
    await page.route('**/api/v1/meetings/audio?*', (route) => route.fulfill({ status: 503 }), { times: 1 });
    await uploadDialog.getByRole('button', { name: 'Распознать запись', exact: true }).click();
    await expect(uploadDialog.getByRole('alert')).toContainText('Не удалось загрузить аудио');
    await expect(uploadDialog.getByRole('button', { name: 'Аудиозапись встречи', exact: true })).toHaveText('Проверка выбора.wav');
    await page.screenshot({ path: testInfo.outputPath('audio-upload-mobile.png'), fullPage: true, animations: 'disabled' });
    await page.keyboard.press('Escape');
    await expect(uploadDialog).toBeHidden();
    // Existing text archives remain supported, though creation now offers audio only.
    const creationResponse = await page.request.post('/api/v1/meetings', {
      headers: { 'X-Requested-With': 'aimeet' }, data: { title, language: 'ru', transcript },
    });
    expect(creationResponse.status()).toBe(201);
    const created = (await creationResponse.json()) as { id: string; language: string };
    createdPath = `/meetings/${created.id}`;
    await page.goto(createdPath);

    await expect(main.getByRole('heading', { name: title, exact: true })).toBeVisible();
    await expectTranscript(page, transcript);
    await expectNoHorizontalOverflow(page);
    await page.reload();
    await expect(main.getByRole('heading', { name: title, exact: true })).toBeVisible();
    await expectTranscript(page, transcript);

    await page
      .getByRole('navigation')
      .getByRole('link', { name: 'Встречи', exact: true })
      .first()
      .click();
    await expect(main.getByRole('heading', { name: 'Встречи', exact: true })).toBeVisible();
    await page.getByLabel('Поиск по названию', { exact: true }).fill(title);
    await page.getByRole('button', { name: 'Найти', exact: true }).click();
    await expect.poll(() => new URL(page.url()).searchParams.get('q')).toBe(title);
    const result = main.getByRole('link', { name: title, exact: true });
    await expect(result).toHaveCount(1);
    await result.click();
    await expectTranscript(page, transcript);

    await page.setViewportSize({ width: 320, height: 740 });
    await expect(main.getByRole('heading', { name: title, exact: true })).toBeVisible();
    await expectTranscript(page, transcript);
    await expectNoHorizontalOverflow(page);

    const deleteTrigger = page.getByRole('button', { name: 'Удалить встречу', exact: true });
    const dialog = page.getByRole('dialog', { name: 'Удалить встречу?', exact: true });
    await deleteTrigger.click();
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveCSS('opacity', '1');
    const dialogBounds = await dialog.boundingBox();
    expect(dialogBounds!.x).toBeGreaterThanOrEqual(0);
    expect(dialogBounds!.x + dialogBounds!.width).toBeLessThanOrEqual(320);
    await expect(dialog.getByRole('button', { name: 'Отмена', exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('delete-dialog-mobile.png'), fullPage: true, animations: 'disabled' });
    // A dialog may render even when CSP rejects Mantine's scroll-lock stylesheet.
    // Verify the production edge nonce authorizes the actual injected style.
    await expectNonceScrollLock(page);
    await expect(dialog.getByRole('button', { name: 'Отмена', exact: true })).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(dialog.getByRole('button', { name: 'Удалить', exact: true })).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(dialog.getByRole('button', { name: 'Отмена', exact: true })).toBeFocused();
    await expectNoHorizontalOverflow(page);
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(deleteTrigger).toBeFocused();
    await expectTranscript(page, transcript);

    await deleteTrigger.click();
    await page.route(`**/api/v1/meetings/${created.id}`, (route) => route.fulfill({ status: 503 }), { times: 1 });
    await dialog.getByRole('button', { name: 'Удалить', exact: true }).click();
    await expect(dialog.getByRole('alert')).toContainText('Не удалось удалить встречу');
    await expect(dialog).toBeVisible();
    await expectTranscript(page, transcript);
    await dialog.getByRole('button', { name: 'Удалить', exact: true }).click();
    await expect(page).toHaveURL(/\/meetings(?:\?.*)?$/);

    // A fresh search reads persisted state rather than relying on the cached list.
    const deletionLookup = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        url.pathname === '/api/v1/meetings' &&
        url.searchParams.get('q') === title &&
        response.request().method() === 'GET'
      );
    });
    await page.goto(`/meetings?q=${encodeURIComponent(title)}`);
    const deletionLookupResponse = await deletionLookup;
    expect(deletionLookupResponse.status()).toBe(200);
    expect(await deletionLookupResponse.json()).toMatchObject({ items: [], total: 0 });
    deleted = true;
    await expect(main.getByRole('heading', { name: 'Встречи', exact: true })).toBeVisible();
    await expect(page.getByLabel('Поиск по названию', { exact: true })).toHaveValue(title);
    await expect(result).toHaveCount(0);
    await expectNoHorizontalOverflow(page);
    await page.getByRole('button', { name: 'Выйти', exact: true }).click();
    await expect(page).toHaveURL(/\/login(?:\?.*)?$/);
    await page.goto('/meetings');
    await expect(page).toHaveURL(/\/login(?:\?.*)?$/);
    await expect(page.getByLabel('Электронная почта', { exact: true })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    expect(browserErrors).toEqual([]);
  } finally {
    if (createdPath && !deleted && !page.isClosed()) {
      try {
        // Cleanup is scoped to the UUID created by this test; never delete other records.
        await page.goto(createdPath);
        await page.getByRole('button', { name: 'Удалить встречу', exact: true }).click();
        await page
          .getByRole('dialog', { name: 'Удалить встречу?', exact: true })
          .getByRole('button', { name: 'Удалить', exact: true })
          .click();
        await expect(page).toHaveURL(/\/meetings(?:\?.*)?$/);
      } catch {
        testInfo.annotations.push({
          type: 'cleanup',
          description: `Could not remove test meeting ${title}.`,
        });
      }
    }
  }
});

test('workspace chat searches existing meetings without manual preparation', async ({ page }, testInfo) => {
  const userId = randomUUID();
  const meetingId = randomUUID();
  const nodeId = randomUUID();
  let indexRequests = 0;
  let searches = 0;
  let polls = 0;
  let ready = false;
  const coverage = () => ({ total: 1, ready: ready ? 1 : 0, pending: !ready && indexRequests ? 1 : 0,
    failed: 0, not_indexed: !ready && !indexRequests ? 1 : 0, unavailable: 0 });
  const chatStore = conversationStore();
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/\/(assistant|rag)\/chat\/stream$/, '/$1/chat');
    if (path.startsWith('/api/v1/assistant/conversations')) return chatStore(route);
    const method = route.request().method();
    if (path === '/api/v1/auth/me') return route.fulfill({ json: { id: userId, email: 'qa@example.com', display_name: 'QA' } });
    if (path === '/api/v1/rag/config') return route.fulfill({ json: { offline: true, llm_provider: 'ollama', llm_model: 'test', reasoning_effort: 'low', embedding_provider: 'ollama', embedding_model: 'test', embedding_dimensions: 3, cloud_configured: false } });
    if (path === '/api/v1/rag/index') {
      if (method === 'POST') indexRequests++;
      else if (indexRequests && ++polls >= 2) ready = true;
      return route.fulfill({ status: method === 'POST' ? 202 : 200, json: coverage() });
    }
    if (path === '/api/v1/assistant/chat') return route.fulfill({ json: {
      action: 'search_meetings', answer: '', search_query: route.request().postDataJSON().question,
    } });
    if (path === '/api/v1/rag/chat') {
      expect(ready).toBe(true);
      if (route.request().postDataJSON().question === 'у нас вообще встречи были?') {
        return route.fulfill({ json: {
          status: 'answered', answer: 'Да, в архиве есть одна встреча.', coverage: coverage(), sources: [],
          catalog_sources: [{ source_id: 'M0', text: 'Сохранено встреч: 1.', meeting_id: null, meeting_title: null }],
          claims: [{ text: 'Да, в архиве есть одна встреча.', citations: [{ kind: 'catalog', source_id: 'M0', quote: 'Сохранено встреч: 1.' }] }],
        } });
      }
      expect(route.request().postDataJSON().question).toContain('бюджет');
      searches++;
      return route.fulfill({ json: {
        status: 'answered', answer: 'Обсуждали на планировании.', coverage: coverage(),
        claims: [{ text: 'Обсуждали на планировании.', citations: [{ source_id: 'S1', node_id: nodeId, start_char: 0, end_char: 19, quote: 'Бюджет согласовали.' }] }],
        sources: [{ source_id: 'S1', node_id: nodeId, parent_id: null, start_char: 0, end_char: 19, text: 'Бюджет согласовали.', reason: 'hit', meeting_id: meetingId, meeting_title: 'Планирование', meeting_created_at: '2026-09-11T09:00:00Z' }],
      } });
    }
    throw new Error(`Unexpected request: ${method} ${path}`);
  });
  await page.goto('/chat');
  await expect(page.getByRole('heading', { name: 'Чат', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /Подготовить|Добавить встречу/ })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Добавить встречу' })).toHaveCount(0);
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.screenshot({ path: testInfo.outputPath('chat-empty-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(async () => Math.round((await page.locator('.sidebar').boundingBox())!.width)).toBe(64);
  await page.screenshot({ path: testInfo.outputPath('chat-empty-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole('button', { name: 'На какой встрече обсуждали бюджет?' }).click();
  const send = page.getByRole('button', { name: 'Отправить вопрос' });
  await expect(send).toBeEnabled();
  await send.click();
  await expect(page.getByText('Готовлю ответ…')).toBeVisible();
  await expect(page.getByText('Обсуждали на планировании.')).toBeVisible();
  expect(indexRequests).toBe(1);
  expect(searches).toBe(1);
  await page.locator('.workspace-chat-source summary').click();
  await expect(page.locator('blockquote')).toHaveText('Бюджет согласовали.');
  await expect(page.getByRole('link', { name: 'Открыть встречу' })).toHaveAttribute('href', `/meetings/${meetingId}`);
  await page.getByRole('textbox', { name: 'Сообщение ассистенту' }).fill('Какой бюджет?');
  await send.click();
  await expect(page.getByText('Обсуждали на планировании.', { exact: true })).toHaveCount(2);
  expect(searches).toBe(2);
  expect(indexRequests).toBe(1);
  await page.getByRole('textbox', { name: 'Сообщение ассистенту' }).fill('у нас вообще встречи были?');
  await send.click();
  await expect(page.getByText('Да, в архиве есть одна встреча.')).toBeVisible();
  await page.getByText('Список встреч', { exact: true }).click();
  await expect(page.getByRole('link', { name: 'Все встречи', exact: true })).toHaveAttribute('href', /\/meetings/);
  await page.setViewportSize({ width: 1920, height: 1080 });
  const header = await page.locator('.workspace-chat-header').boundingBox();
  const conversation = await page.locator('.workspace-chat-conversation').boundingBox();
  const composer = await page.locator('.workspace-chat-composer-wrap').boundingBox();
  expect(header!.x).toBeLessThan(600);
  expect(Math.abs(header!.x - conversation!.x)).toBeLessThan(2);
  expect(Math.abs(header!.x - composer!.x)).toBeLessThan(2);
  await page.screenshot({ path: testInfo.outputPath('chat-answer-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 320, height: 740 });
  await expectNoHorizontalOverflow(page);
});

test('audio upload shows progressive results and keeps the sidebar on the left', async ({ page }, testInfo) => {
  const id = '22222222-2222-4222-8222-222222222222';
  let stage = 0;
  const segments = [
    { start: 0, end: 4, text: 'Обсудим запуск пилота.' },
    { start: 5, end: 12, text: 'Алия подготовит смету к пятнице.' },
  ];
  const user = { id: '11111111-1111-4111-8111-111111111111', email: 'preview@example.com', display_name: 'Проверка интерфейса' };
  await page.route('**/api/v1/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const body = {
      id, title: 'Пилот', language: 'ru', source_type: 'audio', status: stage >= 2 ? 'transcribed' : 'draft',
      created_at: '2026-09-11T09:00:00Z', updated_at: '2026-09-11T09:00:00Z', audio_filename: 'Пилот.wav', audio_bytes: 100,
      transcript: segments.slice(0, stage + 1).map((segment) => segment.text).join('\n'), transcript_length: 55,
      segments: segments.slice(0, stage + 1),
      transcription: { status: stage >= 2 ? 'succeeded' : 'running', progress: stage >= 2 ? 100 : 30, error_code: null, detected_language: 'ru', duration_seconds: 20 },
    };
    let json: unknown;
    if (pathname === '/api/v1/auth/me') json = user;
    else if (pathname === '/api/v1/meetings') json = { items: [], total: 0, limit: 20, offset: 0 };
    else if (pathname === '/api/v1/meetings/audio' || pathname === `/api/v1/meetings/${id}`) json = body;
    else if (pathname.endsWith('/board')) json = {
      version: stage, status: stage < 2 ? 'idle' : stage === 2 ? 'running' : 'ready', progress: stage < 2 ? 0 : stage === 2 ? 45 : 100,
      error_code: null, cards: [], summary: stage < 2 ? [] : [{ text: 'Алия подготовит смету.', quote: segments[1].text }],
    };
    else if (pathname === '/api/v1/rag/config') json = { offline: true, llm_provider: 'ollama', llm_model: 'test', reasoning_effort: '', embedding_provider: 'ollama', embedding_model: 'test', embedding_dimensions: 768, cloud_configured: false };
    else if (pathname.endsWith('/rag/index')) json = { index_id: null, status: 'not_indexed', node_count: 0, error_code: null };
    else { await route.abort(); return; }
    await route.fulfill({ json });
  });
  await page.goto('/meetings');
  await expect(page.getByRole('heading', { name: 'Встречи', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Добавить встречу', exact: true })).toHaveCount(0);
  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'Загрузить аудио', exact: true }).first().click();
  await (await chooser).setFiles({ name: 'Пилот.wav', mimeType: 'audio/wav', buffer: Buffer.from('browser fixture; no actual inference') });
  await expect(page.getByLabel('Название встречи', { exact: true })).toHaveValue('Пилот');
  await page.getByRole('button', { name: 'Распознать запись', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/meetings/${id}$`));
  await expect(page.getByText(segments[0].text, { exact: true })).toBeVisible();
  await expect(page.getByText(segments[1].text, { exact: true })).toHaveCount(0);
  stage = 1;
  await expect(page.getByText(segments[1].text, { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Свернуть боковую панель', exact: true }).click();
  await expect(page.locator('.workspace-frame')).toHaveClass(/sidebar-collapsed/);
  await page.reload();
  await expect(page.getByRole('button', { name: 'Развернуть боковую панель', exact: true })).toBeVisible();
  await page.setViewportSize({ width: 320, height: 740 });
  await expect.poll(() => page.locator('.sidebar').evaluate((element) => Math.round(element.getBoundingClientRect().width))).toBe(64);
  await expectNoHorizontalOverflow(page);
  await page.getByRole('button', { name: 'Развернуть боковую панель', exact: true }).click();
  await expect(page.locator('.workspace-frame')).toHaveClass(/sidebar-mobile-expanded/);
  await page.getByRole('button', { name: 'Свернуть боковую панель', exact: true }).click();
  stage = 2;
  await expect(page.getByRole('status')).toContainText('Готовим итоги');
  await page.getByRole('tab', { name: 'Итоги', exact: true }).click();
  await expect(page.getByText('Алия подготовит смету.', { exact: true })).toBeVisible();
  await expect(page.getByText(/Промежуточные выводы могут уточняться/)).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: testInfo.outputPath('audio-stream-mobile.png'), fullPage: true, animations: 'disabled' });
  stage = 3;
  await expect(page.locator('.meeting-processing')).toHaveCount(0);
  await expect(page.getByRole('tab', { name: 'Канбан', exact: true })).toBeVisible();
  await expect(page.getByText(/Промежуточные выводы могут уточняться/)).toHaveCount(0);
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.screenshot({ path: testInfo.outputPath('audio-stream-desktop.png'), fullPage: true, animations: 'disabled' });
});

test('live connection keeps the sidebar and places microphone with meeting actions', async ({ page }, testInfo) => {
  const roomId = '33333333-3333-4333-8333-333333333333';
  const grant = { room_id: roomId, participant_id: '44444444-4444-4444-8444-444444444444', name: 'Организатор', is_host: true, member_token: 'fixture', media_token: 'fixture', media_url: '/media' };
  await page.addInitScript((value) => {
    sessionStorage.setItem(`soyle-live:${value.room_id}`, JSON.stringify(value));
    sessionStorage.setItem(`soyle-live-autojoin:${value.room_id}`, '1');
  }, grant);
  let releaseConnection!: () => void;
  const connectionGate = new Promise<void>((resolve) => { releaseConnection = resolve; });
  await page.route('**/api/v1/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith('/token')) {
      await connectionGate;
      await route.fulfill({ status: 503, json: {} });
    } else if (pathname === '/api/v1/auth/me') await route.fulfill({ json: { id: grant.participant_id, display_name: 'Организатор', email: 'preview@example.com' } });
    else if (pathname === `/api/v1/live/rooms/${roomId}`) await route.fulfill({ json: {
      id: roomId, title: 'Новая встреча', language: 'ru', status: 'active', created_at: '2026-09-11T09:00:00Z', ended_at: null, meeting_id: null,
      participants: [], utterances: [], insights: [], transcript_revision: 0, analysis_status: 'idle', analysis_error: null, audio_error: null, analysis_provider: 'test', can_end: true,
    } });
    else await route.abort();
  });
  await page.goto(`/live/${roomId}`);
  await expect(page.getByRole('heading', { name: 'Подключаемся к разговору', exact: true })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Основная навигация' })).toBeVisible();
  await page.getByRole('button', { name: 'Свернуть боковую панель', exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath('live-connecting.png'), fullPage: true, animations: 'disabled' });
  releaseConnection();
  await expect(page.getByText('Не удалось подключить голосовую связь.', { exact: false })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Подключаемся к разговору', exact: true })).toHaveCount(0);
  await expect(page.locator('.live-room-actions').getByRole('button', { name: 'Включить микрофон', exact: true })).toBeVisible();
  await expect(page.locator('.live-room-actions').getByRole('button', { name: 'Пригласить', exact: true })).toBeVisible();
  await expect(page.locator('.live-topbar')).toHaveCount(0);
  await expect(page.locator('.live-reveal')).toHaveCSS('opacity', '1');
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: testInfo.outputPath('live-compact-desktop.png'), fullPage: true, animations: 'disabled' });
  await page.setViewportSize({ width: 320, height: 740 });
  await expectNoHorizontalOverflow(page);
  await expect.poll(() => page.locator('.sidebar').evaluate((element) => Math.round(element.getBoundingClientRect().width))).toBe(64);
  await page.screenshot({ path: testInfo.outputPath('live-compact-mobile.png'), fullPage: true, animations: 'disabled' });
});

test('new conversation starts with an automatic title and Russian language', async ({ page }, testInfo) => {
  let payload: { title: string; language: string } | undefined;
  await page.route('**/api/v1/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/api/v1/auth/me') await route.fulfill({ json: { id: '11111111-1111-4111-8111-111111111111', email: 'preview@example.com', display_name: 'Проверка интерфейса' } });
    else if (pathname === '/api/v1/live/rooms' && route.request().method() === 'POST') {
      payload = route.request().postDataJSON();
      await route.fulfill({ status: 503, json: {} });
    } else if (pathname === '/api/v1/live/rooms') await route.fulfill({ json: [] });
    else await route.abort();
  });
  await page.goto('/live');
  await expect(page.getByRole('button', { name: 'Новый разговор', exact: true })).toBeVisible();
  await expect(page.getByRole('textbox')).toHaveCount(0);
  await expect(page.getByRole('combobox')).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath('live-entry-compact.png'), fullPage: true, animations: 'disabled' });
  await page.getByRole('button', { name: 'Новый разговор', exact: true }).click();
  await expect.poll(() => payload?.language).toBe('ru');
  expect(payload?.title).toMatch(/^Разговор · /);
  await expect(page.getByRole('alert')).toContainText('Голосовая связь пока недоступна');
  await page.setViewportSize({ width: 320, height: 740 });
  await expectNoHorizontalOverflow(page);
});

test('assistant chats with history and helps without touching meeting search', async ({ page }) => {
  let messages = 0;
  const chatStore = conversationStore();
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/\/(assistant|rag)\/chat\/stream$/, '/$1/chat');
    if (path.startsWith('/api/v1/assistant/conversations')) return chatStore(route);
    if (path === '/api/v1/auth/me') return route.fulfill({ json: { id: randomUUID(), email: 'qa@example.com', display_name: 'QA' } });
    if (path === '/api/v1/rag/config') return route.fulfill({ json: { offline: false, llm_provider: 'openai', llm_model: 'gpt-5.6-luna', reasoning_effort: 'max', embedding_provider: 'openai', embedding_model: 'test', embedding_dimensions: 3, cloud_configured: true } });
    if (path === '/api/v1/assistant/chat') {
      messages++;
      const body = route.request().postDataJSON();
      let answer = 'Привет, Алия! Чем помочь?';
      if (messages === 1) expect(body.history).toEqual([]);
      if (messages === 2) {
        expect(body.history).toEqual([
          { role: 'user', content: 'Привет, меня зовут Алия' },
          { role: 'assistant', content: 'Привет, Алия! Чем помочь?' },
        ]);
        answer = 'Вас зовут Алия.';
      }
      if (messages === 3) answer = 'Откройте «Встречи» и нажмите «Загрузить аудио».';
      if (messages === 4) { expect(body.history).toEqual([]); answer = 'Привет! Чем могу помочь?'; }
      if (messages === 5) {
        expect(body.history[0].content).toBe('Привет, меня зовут Алия');
        expect(body.history).toHaveLength(6);
        answer = 'В этом чате вы представились как Алия.';
      }
      return route.fulfill({ json: { action: 'reply', answer, search_query: '' } });
    }
    throw new Error(`Conversation must not access meetings or embeddings: ${path}`);
  });
  await page.goto('/chat');
  await expect(page.getByRole('heading', { name: 'Чем могу помочь?' })).toBeVisible();
  await expect(page.getByText(/Вопросы и фрагменты встреч передаются/)).toHaveCount(0);
  const input = page.getByRole('textbox', { name: 'Сообщение ассистенту' });
  const send = page.getByRole('button', { name: 'Отправить вопрос' });
  await input.fill('Привет, меня зовут Алия');
  await send.click();
  await expect(page.getByText('Привет, Алия! Чем помочь?', { exact: true })).toBeVisible();
  await input.fill('Как меня зовут?');
  await send.click();
  await expect(page.getByText('Вас зовут Алия.', { exact: true })).toBeVisible();
  await input.fill('Как загрузить запись?');
  await send.click();
  await expect(page.getByText('Откройте «Встречи» и нажмите «Загрузить аудио».', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Новый чат', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Чем могу помочь?' })).toBeVisible();
  await input.fill('Привет');
  await send.click();
  await expect(page.getByText('Привет! Чем могу помочь?', { exact: true })).toBeVisible();
  const chatList = page.getByRole('navigation', { name: 'Список чатов' });
  await chatList.getByRole('button', { name: 'Привет, меня зовут Алия', exact: true }).click();
  await expect(page.getByText('Вас зовут Алия.', { exact: true })).toBeVisible();
  await expect(page.getByText('Привет! Чем могу помочь?', { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByText('Вас зовут Алия.', { exact: true })).toBeVisible();
  await input.fill('Что ты обо мне знаешь?');
  await send.click();
  await expect(page.getByText('В этом чате вы представились как Алия.', { exact: true })).toBeVisible();
  expect(messages).toBe(5);
  await page.setViewportSize({ width: 390, height: 844 });
  await expectNoHorizontalOverflow(page);
});

test('live transcript recovers after a temporary failure without hiding recent speech', async ({ page }) => {
  const roomId = '55555555-5555-4555-8555-555555555555';
  const participantId = '66666666-6666-4666-8666-666666666666';
  const grant = { room_id: roomId, participant_id: participantId, name: 'Участник', is_host: false, member_token: 'fixture', media_token: '', media_url: '/livekit' };
  const recent = { id: '77777777-7777-4777-8777-777777777777', participant_id: participantId, speaker: 'Участник', start: 30, end: 35, text: 'Последняя реплика остаётся видна.' };
  const earlier = { ...recent, id: '88888888-8888-4888-8888-888888888888', start: 0, end: 5, text: 'Ранняя реплика загружена после восстановления.' };
  let calls = 0;
  let recovered = false;
  await page.addInitScript((value) => {
    sessionStorage.setItem(`soyle-live:${value.room_id}`, JSON.stringify(value));
    sessionStorage.setItem(`soyle-live-autojoin:${value.room_id}`, '1');
  }, grant);
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/transcript')) {
      calls += 1;
      await route.fulfill(!recovered ? { status: 503, json: {} } : { json: [earlier, recent] });
    } else if (url.pathname.endsWith(`/rooms/${roomId}`)) await route.fulfill({ json: {
      id: roomId, title: 'История разговора', language: 'ru', status: 'ended', created_at: '2026-09-11T09:00:00Z', ended_at: '2026-09-11T09:10:00Z', meeting_id: null,
      participants: [{ id: participantId, name: 'Участник', is_host: false }], utterances: [recent], insights: [], transcript_revision: 2,
      analysis_status: 'idle', analysis_error: null, audio_error: null, analysis_provider: 'test', can_end: false,
    } });
    else await route.abort();
  });
  await page.goto(`/live/${roomId}?view=conversation`);
  await expect(page.getByText(recent.text, { exact: true })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('Повторим загрузку автоматически');
  await expect(page.getByText(recent.text, { exact: true })).toBeVisible();
  recovered = true;
  await expect(page.getByText(earlier.text, { exact: true })).toBeVisible({ timeout: 15000 });
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByText(recent.text, { exact: true })).toHaveCount(1);
  expect(calls).toBeGreaterThanOrEqual(3);
});

for (const sourceType of ['text', 'audio'] as const) {
  test(`saved ${sourceType} meeting uses live-style Conversation Outcomes Kanban tabs`, async ({ page }, testInfo) => {
    const id = '55555555-5555-4555-8555-555555555555';
    const transcript = 'Алия: подготовлю смету к пятнице.\nДанияр: согласовали запуск пилота.';
    let boardStatus: 'idle' | 'ready' = 'ready';
    let failBoard = false;
    const card = {
      id: '66666666-6666-4666-8666-666666666666', kind: 'task', title: 'Подготовить смету',
      description: '', assignee: 'Алия', due_date: null, due_text: 'к пятнице', priority: 'unspecified',
      status: 'todo', reviewed: false, quote: 'Алия: подготовлю смету к пятнице.', quote_start: 0,
      agreement: 'confirmed', origin: 'ai', start_char: 0, end_char: 32,
      evidence: null, revisions: [], clarifications: [],
    };
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/api/v1/**', async (route) => {
      const path = new URL(route.request().url()).pathname.replace(/\/(assistant|rag)\/chat\/stream$/, '/$1/chat');
      if (path.endsWith('/auth/me')) return route.fulfill({ json: {
        id: '11111111-1111-4111-8111-111111111111', email: 'preview@example.com', display_name: 'Тестовый профиль',
      } });
      if (path === `/api/v1/meetings/${id}`) return route.fulfill({ json: {
        id, title: 'Тест вкладок — данные проверки', language: 'ru', source_type: sourceType,
        status: sourceType === 'audio' ? 'transcribed' : 'draft',
        created_at: '2026-09-11T09:00:00Z', updated_at: '2026-09-11T09:00:00Z',
        audio_filename: sourceType === 'audio' ? 'Пример.wav' : null, audio_bytes: null,
        transcript, transcript_length: transcript.length, segments: null,
        transcription: sourceType === 'audio' ? { status: 'succeeded', progress: 100, error_code: null, detected_language: 'ru', duration_seconds: 20 } : null,
      } });
      if (path.endsWith('/board')) return route.fulfill(failBoard ? { status: 503, json: {} } : { json: {
        version: 1, status: boardStatus, error_code: null, progress: boardStatus === 'ready' ? 100 : 0,
        cards: boardStatus === 'ready' ? [card] : [],
        summary: boardStatus === 'ready' ? [{ text: 'Участники согласовали запуск пилота.', quote: 'Данияр: согласовали запуск пилота.', evidence: null }] : [],
      } });
      if (path.endsWith('/rag/config')) return route.fulfill({ json: { offline: true, llm_provider: 'ollama', llm_model: 'test', reasoning_effort: '', embedding_provider: 'ollama', embedding_model: 'test', embedding_dimensions: 768, cloud_configured: false } });
      if (path.endsWith('/rag/index')) return route.fulfill({ json: { index_id: null, status: 'not_indexed', node_count: 0, error_code: null } });
      return route.fulfill({ status: 404, json: {} });
    });
    await page.goto(`/meetings/${id}`);
    const tabs = page.getByRole('tablist', { name: 'Разделы встречи', exact: true });
    await expect(tabs.getByRole('tab')).toHaveText(['Разговор', 'Итоги', 'Канбан']);
    await expect(page.getByRole('tabpanel', { name: 'Разговор', exact: true })).toBeVisible();
    await expectTranscript(page, transcript);
    await expect(page.locator('.kanban-grid')).toHaveCount(0);
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath('meeting-conversation.png'), fullPage: true });

    await page.getByRole('tab', { name: 'Итоги', exact: true }).click();
    await expect(page).toHaveURL(/view=insights/);
    await expect(page.getByText('Подготовить смету', { exact: true })).toBeVisible();
    await expect(page.getByTestId('transcript')).toHaveCount(0);
    await expect(page.locator('.kanban-grid')).toHaveCount(0);
    await expect(page.locator('.saved-insight-note blockquote')).toHaveText(card.quote);
    await page.locator('.saved-insight-note').getByRole('button', { name: 'Открыть фрагмент', exact: true }).click();
    const sourceDialog = page.getByRole('dialog', { name: 'Источник из встречи' });
    await expect(sourceDialog.locator('mark')).toHaveText(card.quote);
    await page.getByRole('button', { name: 'Закрыть источник' }).click();
    await page.screenshot({ path: testInfo.outputPath('meeting-insights.png'), fullPage: true });
    await page.locator('.saved-insight-note').getByRole('button', { name: 'К разговору' }).click();
    await expect(page.getByTestId('transcript')).toBeFocused();
    await expect(page.getByTestId('transcript').locator('mark')).toHaveText(card.quote);

    await page.getByRole('tab', { name: 'Канбан', exact: true }).click();
    await expect(page).toHaveURL(/view=kanban/);
    await expect(page.getByRole('region', { name: 'К выполнению', exact: true }).getByRole('button', { name: 'Подготовить смету', exact: true })).toBeVisible();
    await expect(page.getByTestId('transcript')).toHaveCount(0);
    await expect(page.getByRole('radiogroup')).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole('tab', { name: 'Канбан', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('button', { name: 'Добавить карточку', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Добавить карточку', exact: true }).click();
    await expect(page.getByRole('dialog', { name: 'Новая карточка' })).toBeVisible();
    await page.getByRole('button', { name: 'Отмена', exact: true }).click();
    await page.screenshot({ path: testInfo.outputPath('meeting-kanban-desktop.png'), fullPage: true });
    await page.emulateMedia({ media: 'print' });
    await expect(page.locator('.saved-meeting-header')).toBeHidden();
    await expect(page.locator('.board-print').getByRole('heading', { name: 'Тест вкладок — данные проверки', exact: true })).toBeVisible();
    await page.pdf({ path: testInfo.outputPath('meeting-protocol.pdf'), format: 'A4' });
    await page.emulateMedia({ media: 'screen' });

    await page.setViewportSize({ width: 320, height: 740 });
    await expect(tabs.getByRole('tab', { name: 'Канбан', exact: true })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath('meeting-kanban-mobile.png'), fullPage: true });
    boardStatus = 'idle';
    await page.reload();
    await expect(page.getByRole('button', { name: 'Добавить карточку', exact: true })).toBeEnabled();
    await expect(page.getByRole('tab', { name: 'Канбан', exact: true })).toBeVisible();
    failBoard = true;
    await page.reload();
    await expect(page.getByRole('button', { name: 'Повторить загрузку', exact: true })).toBeVisible();
    await expect(page.locator('.kanban-card')).toHaveCount(0);
    await page.getByRole('tab', { name: 'Разговор', exact: true }).click();
    await expectTranscript(page, transcript);
    expect(errors).toEqual([]);
  });
}

test('saved live outcomes appear in insights and kanban even without tasks', async ({ page }, testInfo) => {
  const mid = '99999999-9999-4999-8999-999999999999';
  const quote = 'Почему не появляется текст разговора?';
  const card = { id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', kind: 'question', title: 'Выяснить причину отсутствия текста', description: '', assignee: null, due_date: null, due_text: null, priority: 'unspecified', status: 'todo', reviewed: false, quote, quote_start: 0, start_char: 0, end_char: quote.length, agreement: 'unclear', origin: 'ai', revisions: [], clarifications: [], evidence: null };
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname.replace(/\/(assistant|rag)\/chat\/stream$/, '/$1/chat');
    if (path === '/api/v1/auth/me') return route.fulfill({ json: { id: mid, display_name: 'Проверка', email: 'qa@example.com' } });
    if (path === `/api/v1/meetings/${mid}`) return route.fulfill({ json: { id: mid, title: 'Сохранённый разговор', language: 'ru', source_type: 'text', status: 'transcribed', transcript: quote, transcript_length: quote.length, segments: null, audio_filename: null, audio_bytes: null, transcription: null, created_at: '2026-09-11T09:00:00Z', updated_at: '2026-09-11T09:00:00Z' } });
    if (path.endsWith('/board')) return route.fulfill({ json: { status: 'ready', version: 1, progress: 100, error_code: null, cards: [card], summary: [{ text: 'Обсудили отображение стенограммы.', quote }] } });
    return route.abort();
  });
  await page.goto(`/meetings/${mid}?view=insights`);
  await expect(page.getByText('Кратко о встрече', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Обсудили отображение стенограммы.', { exact: true })).toHaveCount(0);
  await expect(page.getByText(card.title, { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Подготовить итоги', exact: true })).toHaveCount(0);
  await expect(page.getByText('Задать вопрос по встрече', { exact: true })).toHaveCount(0);
  await page.getByRole('tab', { name: 'Канбан', exact: true }).click();
  await expect(page.getByRole('combobox', { name: 'Группировка карточек' })).toHaveValue('По содержанию встречи');
  await expect(page.getByRole('button', { name: card.title, exact: true })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: testInfo.outputPath('saved-live-kanban.png'), fullPage: true, animations: 'disabled' });
});

test('assistant cites outcomes and shows real task creation progress with safe retry', async ({ page }, testInfo) => {
  const meetingId = randomUUID();
  const cardId = randomUUID();
  let createPlans = 0;
  let writes = 0;
  let operationId: string | undefined;
  let finishFirstWrite: (() => void) | undefined;
  const coverage = { total: 1, ready: 1, pending: 0, failed: 0, not_indexed: 0, unavailable: 0 };
  const chatStore = conversationStore();
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/\/(assistant|rag)\/chat\/stream$/, '/$1/chat');
    if (path.startsWith('/api/v1/assistant/conversations')) return chatStore(route);
    if (path === '/api/v1/auth/me') return route.fulfill({ json: { id: '11111111-1111-4111-8111-111111111111', email: 'qa@example.com', display_name: 'QA' } });
    if (path === '/api/v1/rag/config') return route.fulfill({ json: { offline: true, llm_provider: 'ollama', llm_model: 'test', reasoning_effort: 'max', embedding_provider: 'ollama', embedding_model: 'test', embedding_dimensions: 3, cloud_configured: false } });
    if (path === '/api/v1/assistant/chat') {
      const create = route.request().postDataJSON().question.startsWith('Добавь');
      if (create) createPlans++;
      return route.fulfill({ json: { action: create ? 'create_tasks' : 'search_meetings', answer: '', search_query: create ? '' : 'Текущие задачи' } });
    }
    if (path === '/api/v1/rag/index') return route.fulfill({ json: coverage });
    if (path === '/api/v1/rag/chat') return route.fulfill({ json: {
      status: 'answered', answer: 'Фронтенд — в работе.', coverage, sources: [], catalog_sources: [],
      board_coverage: { available_sources: 2, selected_sources: 2 },
      board_sources: [
        { source_id: 'B1', kind: 'kanban', meeting_id: meetingId, meeting_title: 'Планирование', card_id: cardId, title: 'Разработать фронтенд', text: 'Статус: В работе', board_version: 1, provisional: false, transcript_current: true, transcript_quote: null },
        { source_id: 'B2', kind: 'summary', meeting_id: meetingId, meeting_title: 'Планирование', card_id: null, title: 'Итоги встречи', text: 'Цель — запустить пилот.', board_version: 1, provisional: false, transcript_current: true, transcript_quote: null },
      ],
      claims: [{ text: 'Фронтенд — в работе.', citations: [{ kind: 'board', source_id: 'B1', quote: 'Статус: В работе' }] },
        { text: 'Цель — запустить пилот.', citations: [{ kind: 'board', source_id: 'B2', quote: 'Цель — запустить пилот.' }] }],
    } });
    if (path === '/api/v1/assistant/tasks') {
      writes++;
      const body = route.request().postDataJSON();
      if (writes === 1) {
        operationId = body.request_id;
        await new Promise<void>((resolve) => { finishFirstWrite = resolve });
        return route.abort(); // Simulate a saved operation whose response was lost.
      }
      expect(body.request_id).toBe(operationId);
      return route.fulfill({ json: { status: 'created', answer: 'Добавлено задач в канбан: 1.', tasks: [{
        meeting_id: meetingId, meeting_title: 'Планирование', card_id: randomUUID(), board_version: 2,
        title: 'Проверить фронтенд', description: '', assignee: 'Алия', due_date: null, due_text: 'к пятнице',
      }] } });
    }
    throw new Error(`Unexpected request ${path}`);
  });
  await page.goto('/chat');
  const input = page.getByRole('textbox', { name: 'Сообщение ассистенту' });
  const send = page.getByRole('button', { name: 'Отправить вопрос' });
  await input.fill('Какие сейчас задачи?');
  await send.click();
  await expect(page.getByText('Фронтенд — в работе.', { exact: true })).toBeVisible();
  await page.getByText('Канбан · Планирование', { exact: true }).click();
  await expect(page.getByRole('link', { name: 'Открыть канбан' })).toHaveAttribute('href', new RegExp(`/meetings/${meetingId}\\?view=kanban`));
  await page.getByText('Итоги · Планирование', { exact: true }).click();
  await expect(page.getByRole('link', { name: 'Открыть итоги' })).toHaveAttribute('href', new RegExp(`/meetings/${meetingId}\\?view=insights`));
  await input.fill('Добавь задачу проверить фронтенд');
  await send.click();
  await expect(page.getByRole('status')).toContainText('Создаю задачи в канбане');
  await expect(page.locator('.workspace-chat-created-task')).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath('assistant-creating.png'), fullPage: true });
  finishFirstWrite!();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Добавь задачу проверить фронтенд', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Повторить отправку' }).click();
  await expect(page.getByText('Добавлено задач в канбан: 1.', { exact: true })).toBeVisible();
  await expect(page.locator('.workspace-chat-created-task')).toContainText('Проверить фронтенд');
  await expect(page.locator('.workspace-chat-created-task')).toHaveAttribute('href', new RegExp(`/meetings/${meetingId}\\?view=kanban`));
  expect(createPlans).toBe(1);
  expect(writes).toBe(2);
  await page.screenshot({ path: testInfo.outputPath('assistant-created.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expectNoHorizontalOverflow(page);
});

test('kanban drag and drop saves status and category, cancels and rolls back failures', async ({ page }, testInfo) => {
  const mid = '77777777-7777-4777-8777-777777777777';
  const makeCard = (id: string, kind: string, title: string) => ({ id, kind, title, description: '', assignee: 'Алия', due_date: null, due_text: null, priority: 'unspecified', status: 'todo', reviewed: false, quote: 'Алия: обсудим запуск.', quote_start: 0, agreement: 'unclear', origin: 'manual', start_char: 0, end_char: 20, evidence: null, revisions: [], clarifications: [] });
  let cards = [makeCard('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'task', 'Проверить смету'), makeCard('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'topic', 'Обсудить запуск')];
  let version = 1;
  let failSave = false;
  const writes: Array<Record<string, unknown>> = [];
  const board = () => ({ version, status: 'ready', progress: 100, error_code: null, cards, summary: [] });
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/\/(assistant|rag)\/chat\/stream$/, '/$1/chat');
    if (path.endsWith('/auth/me')) return route.fulfill({ json: { id: mid, email: 'qa@example.test', display_name: 'Тест DnD' } });
    if (path === `/api/v1/meetings/${mid}`) return route.fulfill({ json: { id: mid, title: 'Тест канбана', language: 'ru', source_type: 'text', status: 'transcribed', transcript: 'Алия: обсудим запуск.', transcript_length: 20, segments: null, transcription: null, audio_filename: null, audio_bytes: null, created_at: '2026-09-11T09:00:00Z', updated_at: '2026-09-11T09:00:00Z' } });
    if (path.endsWith('/board')) return route.fulfill({ json: board() });
    if (path.includes('/board/cards/')) {
      const payload = route.request().postDataJSON() as Record<string, unknown>;
      writes.push(payload);
      if (failSave) return route.fulfill({ status: 503, json: { error: { code: 'TEST_UNAVAILABLE' } } });
      expect(payload.version).toBe(version);
      const id = path.split('/').at(-1);
      cards = cards.map((card) => card.id === id ? { ...card, ...payload } : card);
      version++;
      return route.fulfill({ json: board() });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto(`/meetings/${mid}?view=kanban`);
  const handle = (title: string) => page.getByRole('button', { name: `Переместить: ${title}`, exact: true });
  async function drag(title: string, target: string, end = true) {
    const origin = handle(title);
    await origin.scrollIntoViewIfNeeded();
    const from = (await origin.boundingBox())!;
    const to = (await page.getByRole('region', { name: target, exact: true }).boundingBox())!;
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(from.x + from.width / 2 + 12, from.y + from.height / 2, { steps: 3 });
    await expect(page.locator('.kanban-drag-preview')).toBeVisible();
    await page.mouse.move(to.x + to.width / 2, to.y + 80, { steps: 15 });
    await expect(page.getByRole('region', { name: target, exact: true })).toHaveClass(/drop-target/);
    if (end) await page.mouse.up();
  }
  await expect(handle('Проверить смету')).toBeVisible();
  const tops = await page.locator('.board-toolbar').evaluate((el) => Array.from(el.children).filter((node) => node.getBoundingClientRect().height > 0).map((node) => node.getBoundingClientRect().top));
  expect(Math.max(...tops) - Math.min(...tops)).toBeLessThanOrEqual(2);
  await page.screenshot({ path: testInfo.outputPath('compact-toolbar.png'), fullPage: true });

  await drag('Проверить смету', 'В работе', false);
  await page.screenshot({ path: testInfo.outputPath('drag-preview.png'), fullPage: true });
  await page.mouse.up();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({ kind: 'task', status: 'doing', quote: cards[0].quote, assignee: 'Алия' });
  await page.reload();
  await expect(page.getByRole('region', { name: 'В работе', exact: true }).getByRole('button', { name: 'Проверить смету', exact: true })).toBeVisible();

  await handle('Проверить смету').focus();
  await page.keyboard.press('Space');
  await expect(page.locator('.kanban-drag-preview')).toBeVisible();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('region', { name: 'Заблокировано', exact: true })).toHaveClass(/drop-target/);
  await page.keyboard.press('Space');
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1].status).toBe('blocked');
  await expect(page.locator('.kanban-drag-preview')).toHaveCount(0);
  await expect(handle('Проверить смету')).toBeFocused();

  await drag('Проверить смету', 'Готово', false);
  await page.keyboard.press('Escape'); await page.mouse.up();
  await expect(page.locator('.kanban-drag-preview')).toHaveCount(0);
  expect(writes.length).toBe(2);
  await drag('Проверить смету', 'Готово', false);
  await page.mouse.move(20, 20, { steps: 8 });
  await page.mouse.up();
  await expect(page.locator('.kanban-drag-preview')).toHaveCount(0);
  expect(writes.length).toBe(2);
  failSave = true;
  await drag('Проверить смету', 'Готово');
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Заблокировано', exact: true }).getByRole('button', { name: 'Проверить смету', exact: true })).toBeVisible();
  expect(cards[0].status).toBe('blocked');
  failSave = false;

  await page.getByRole('combobox', { name: 'Группировка карточек', exact: true }).click();
  await page.getByRole('option', { name: 'По содержанию встречи', exact: true }).click();
  await drag('Обсудить запуск', 'Решения');
  await expect.poll(() => writes.length).toBe(4);
  expect(writes[3]).toMatchObject({ kind: 'decision', status: 'todo', quote: 'Алия: обсудим запуск.' });
  await page.reload();
  await page.getByRole('combobox', { name: 'Группировка карточек', exact: true }).click();
  await page.getByRole('option', { name: 'По содержанию встречи', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Решения', exact: true }).getByRole('button', { name: 'Обсудить запуск', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Фильтры', exact: true }).click();
  await page.getByRole('combobox', { name: 'Ответственный', exact: true }).click();
  await page.getByRole('option', { name: 'Алия', exact: true }).click();
  await expect(page.getByRole('combobox', { name: 'Ответственный', exact: true })).toHaveValue('Алия');
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 390, height: 844 });
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: testInfo.outputPath('compact-toolbar-mobile.png'), fullPage: true });
  // Touch input goes through Chrome's input pipeline and the same pointer sensor.
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole('combobox', { name: 'Группировка карточек', exact: true }).click();
  await page.getByRole('option', { name: 'По статусам поручений', exact: true }).click();
  await handle('Проверить смету').scrollIntoViewIfNeeded();
  const fromTouch = (await handle('Проверить смету').boundingBox())!;
  const toTouch = (await page.getByRole('region', { name: 'Готово', exact: true }).boundingBox())!;
  const cdp = await page.context().newCDPSession(page);
  const x = fromTouch.x + fromTouch.width / 2, y = fromTouch.y + fromTouch.height / 2;
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x + 12, y }] });
  await expect(page.locator('.kanban-drag-preview')).toBeVisible();
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: toTouch.x + toTouch.width / 2, y: toTouch.y + 80 }] });
  await expect(page.getByRole('region', { name: 'Готово', exact: true })).toHaveClass(/drop-target/);
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await expect.poll(() => writes.length).toBe(5);
  expect(writes[4].status).toBe('done');
  await cdp.detach();
  expect(errors).toEqual([]);
});

for (const audioAvailable of [true, false]) {
  test(`saved call ${audioAvailable ? 'plays full recording and individual utterances' : 'reports missing historical audio'}`, async ({ page }) => {
    const mid = 'b5c49628-3e54-4bd3-a2d1-7159b4891fc0';
    const cid = '1b0c23e9-03cc-49c6-9532-db1505fdc6ed';
    const quote = 'Проверочная реплика.';
    const transcript = `[00:02] Алия: ${quote}`;
    const start = Array.from('[00:02] Алия: ').length;
    function wav(seconds: number) {
      const samples = seconds * 16000;
      const bytes = Buffer.alloc(44 + samples * 2);
      bytes.write('RIFF', 0); bytes.writeUInt32LE(36 + samples * 2, 4); bytes.write('WAVEfmt ', 8);
      bytes.writeUInt32LE(16, 16); bytes.writeUInt16LE(1, 20); bytes.writeUInt16LE(1, 22);
      bytes.writeUInt32LE(16000, 24); bytes.writeUInt32LE(32000, 28); bytes.writeUInt16LE(2, 32);
      bytes.writeUInt16LE(16, 34); bytes.write('data', 36); bytes.writeUInt32LE(samples * 2, 40);
      for (let i = 0; i < samples; i++) bytes.writeInt16LE(Math.round(800 * Math.sin(2 * Math.PI * 440 * i / 16000)), 44 + i * 2);
      return bytes;
    }
    await page.route('**/api/v1/**', async route => {
      const path = new URL(route.request().url()).pathname.replace(/\/(assistant|rag)\/chat\/stream$/, '/$1/chat');
      if (path.endsWith('/auth/me')) return route.fulfill({ json: { id: mid, email: 'fixture@example.com', display_name: 'Проверка' } });
      if (path === `/api/v1/meetings/${mid}`) return route.fulfill({ json: {
        id: mid, title: 'ТЕСТ записи звонка', language: 'ru', status: 'transcribed', source_type: audioAvailable ? 'audio' : 'text',
        created_at: '2026-09-11T10:00:00Z', updated_at: '2026-09-11T10:00:00Z',
        transcript, transcript_length: transcript.length, audio_filename: audioAvailable ? 'Беседа.wav' : null, audio_bytes: audioAvailable ? 256044 : null,
        transcription: null, segments: [{ start: 2, end: 3, text: `Алия: ${quote}` }],
      } });
      if (path.endsWith('/audio-clips')) return route.fulfill({ json: {
        source: 'live', full_audio_available: audioAvailable, recording_status: audioAvailable ? 'ready' : 'none',
        clips: [{ id: cid, speaker: 'Алия', start: 2, end: 3, start_char: start, end_char: start + Array.from(quote).length, available: audioAvailable }],
      } });
      if (path.endsWith('/audio') || path.endsWith(`/audio-clips/${cid}`)) {
        if (!audioAvailable) return route.fulfill({ status: 404, json: {} });
        const body = wav(path.endsWith('/audio') ? 8 : 1);
        const range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers()['range'] ?? '');
        const startByte = range ? Number(range[1]) : 0;
        const endByte = range?.[2] ? Math.min(Number(range[2]), body.length - 1) : body.length - 1;
        return route.fulfill({ status: range ? 206 : 200, contentType: 'audio/wav',
          headers: { 'Accept-Ranges': 'bytes', ...(range ? { 'Content-Range': `bytes ${startByte}-${endByte}/${body.length}` } : {}) },
          body: body.subarray(startByte, endByte + 1) });
      }
      if (path.endsWith('/board')) return route.fulfill({ json: {
        version: 1, status: 'ready', progress: 100, error_code: null, summary: [], cards: [{
          id: cid, kind: 'topic', title: 'Проверить запись беседы', description: '', assignee: null, due_date: null, due_text: null, priority: 'unspecified',
          status: 'todo', reviewed: false, quote, quote_start: start, start_char: start, end_char: start + Array.from(quote).length,
          agreement: 'unclear', origin: 'ai', evidence: null, revisions: [], clarifications: [],
        }],
      } });
      return route.fulfill({ status: 404, json: {} });
    });
    await page.goto(`/meetings/${mid}?view=conversation`);
    const full = page.locator('audio[aria-label="Аудиозапись встречи"]');
    if (audioAvailable) {
      await expect(page.getByText('Запись всей беседы', { exact: true })).toBeVisible();
      await full.evaluate(async (audio: HTMLAudioElement) => { await audio.play() });
      await expect.poll(() => full.evaluate((audio: HTMLAudioElement) => audio.duration)).toBe(8);
      await full.evaluate((audio: HTMLAudioElement) => { audio.currentTime = 6; audio.pause() });
      await expect.poll(() => full.evaluate((audio: HTMLAudioElement) => audio.currentTime)).toBeGreaterThanOrEqual(6);
      await expect(page.getByRole('button', { name: 'Прослушать реплику', exact: true })).toBeVisible();
    } else {
      await expect(full).toHaveCount(0);
      await expect(page.getByText('Полная аудиозапись этой беседы не сохранилась.')).toBeVisible();
    }
    await page.getByRole('tab', { name: 'Итоги', exact: true }).click();
    await expect(page.locator('.saved-insight-source blockquote')).toHaveText(quote);
    if (audioAvailable) {
      await page.getByRole('button', { name: 'Прослушать момент', exact: true }).click();
      const clip = page.locator('audio[aria-label="Аудио реплики"]');
      await expect.poll(() => clip.evaluate((audio: HTMLAudioElement) => audio.duration)).toBe(1);
      await expect(clip).toHaveAttribute('src', `/api/v1/meetings/${mid}/audio-clips/${cid}`);
      const handle = await clip.elementHandle();
      await page.getByRole('button', { name: 'Закрыть источник', exact: true }).click();
      await expect.poll(() => handle!.evaluate((audio: HTMLAudioElement) => audio.paused)).toBe(true);
    } else {
      await expect(page.getByRole('button', { name: 'Прослушать момент', exact: true })).toHaveCount(0);
      await expect(page.getByText('Аудио этого момента не сохранилось.')).toBeVisible();
      await page.getByRole('button', { name: 'Открыть фрагмент', exact: true }).click();
      await expect(page.getByText('Аудио этого фрагмента не сохранено. Доступна только стенограмма.')).toBeVisible();
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await expectNoHorizontalOverflow(page);
  });
}

test('card editor is wide and saves and clears a Russian Mantine calendar date', async ({ page }, testInfo) => {
  const mid = '12121212-1212-4212-8212-121212121212';
  let version = 1;
  let card = { id: '34343434-3434-4434-8434-343434343434', kind: 'task', title: 'Подготовить смету', description: '', assignee: null, due_date: '2026-09-18' as string | null, due_text: 'К пятнице', priority: 'unspecified', status: 'todo', reviewed: false, quote: null, quote_start: null, agreement: 'unclear', origin: 'manual', start_char: null, end_char: null, evidence: null, revisions: [], clarifications: ['agreement_unconfirmed', 'assignee_missing', 'deadline_missing', 'priority_missing'] };
  const writes: Array<Record<string, unknown>> = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname.replace(/\/(assistant|rag)\/chat\/stream$/, '/$1/chat');
    if (path.endsWith('/auth/me')) return route.fulfill({ json: { id: mid, email: 'preview@example.test', display_name: 'Проверка календаря' } });
    if (path === `/api/v1/meetings/${mid}`) return route.fulfill({ json: { id: mid, title: 'Тест календаря', language: 'ru', status: 'transcribed', source_type: 'text', transcript: 'Обсудим смету.', transcript_length: 13, transcription: null, segments: null, audio_filename: null, audio_bytes: null, created_at: '2026-09-11T09:00:00Z', updated_at: '2026-09-11T09:00:00Z' } });
    if (path.endsWith(`/cards/${card.id}`)) {
      const payload = route.request().postDataJSON() as Record<string, unknown>;
      expect(payload.version).toBe(version);
      writes.push(payload);
      card = { ...card, ...payload };
      version++;
    }
    if (path.includes('/board')) return route.fulfill({ json: { version, status: 'ready', error_code: null, cards: [card], summary: [], progress: 100 } });
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto(`/meetings/${mid}?view=kanban`);
  await page.getByRole('button', { name: 'Подготовить смету', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Карточка встречи', exact: true });
  await expect(dialog).toBeVisible();
  expect((await dialog.boundingBox())!.width).toBeGreaterThanOrEqual(1000);
  const save = dialog.getByRole('button', { name: 'Сохранить карточку', exact: true });
  await expect(save).toBeInViewport();
  await expect(dialog.locator('input[type="date"]')).toHaveCount(0);
  const date = dialog.getByLabel('Дата выполнения', { exact: true });
  await expect(date).toHaveText('18.09.2026');
  await page.screenshot({ path: testInfo.outputPath('wide-card-editor.png'), fullPage: true });
  await date.click();
  const day = page.getByRole('button', { name: '19 сентября 2026', exact: true });
  await expect(day).toBeVisible();
  await expect(page.getByRole('button', { name: 'Следующий месяц', exact: true })).toBeVisible();
  await expect(page.locator('[data-dates-dropdown]')).toHaveCSS('opacity', '1');
  await page.screenshot({ path: testInfo.outputPath('mantine-calendar.png'), fullPage: true, animations: 'disabled' });
  await day.click();
  await expect(date).toHaveText('19.09.2026');
  await save.click();
  await expect(dialog).toHaveCount(0);
  expect(writes[0]).toMatchObject({ due_date: '2026-09-19', due_text: 'К пятнице' });
  await page.reload();
  await page.getByRole('button', { name: 'Подготовить смету', exact: true }).click();
  await expect(date).toHaveText('19.09.2026');
  await page.setViewportSize({ width: 390, height: 844 });
  await expectNoHorizontalOverflow(page);
  await expect(save).toBeInViewport();
  await date.click();
  const calendar = page.getByRole('dialog', { name: 'Выберите дату выполнения', exact: true });
  await expect(calendar).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expect(calendar).toHaveCSS('opacity', '1');
  await page.screenshot({ path: testInfo.outputPath('mantine-calendar-mobile.png'), fullPage: true, animations: 'disabled' });
  await calendar.getByRole('button', { name: '20 сентября 2026', exact: true }).click();
  await expect(calendar).toHaveCount(0);
  await expect(date).toHaveText('20.09.2026');
  await dialog.getByRole('button', { name: 'Очистить дату', exact: true }).click();
  await expect(date).toHaveText('Выберите дату');
  await save.click();
  await expect(dialog).toHaveCount(0);
  expect(writes[1]).toMatchObject({ due_date: null, due_text: 'К пятнице' });
  await page.setViewportSize({ width: 320, height: 740 });
  await page.getByRole('button', { name: 'Подготовить смету', exact: true }).click();
  await expectNoHorizontalOverflow(page);
  await expect(save).toBeInViewport();
  await page.screenshot({ path: testInfo.outputPath('wide-card-editor-mobile.png'), fullPage: true });
  expect(errors).toEqual([]);
});

test('pending message survives navigation and finishes in its original chat', async ({ page }) => {
  const chatStore = conversationStore();
  const uid = randomUUID();
  let release: (() => void) | undefined;
  let plans = 0;
  let pendingSaves = 0;
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/\/(assistant|rag)\/chat\/stream$/, '/$1/chat');
    if (path.startsWith('/api/v1/assistant/conversations')) {
      if (path.endsWith('/pending')) pendingSaves++;
      return chatStore(route);
    }
    if (path === '/api/v1/auth/me') return route.fulfill({ json: { id: uid, email: 'qa@example.com', display_name: 'QA' } });
    if (path === '/api/v1/rag/config') return route.fulfill({ json: { offline: true, llm_provider: 'ollama', llm_model: 'test', reasoning_effort: 'max', embedding_provider: 'ollama', embedding_model: 'test', embedding_dimensions: 3, cloud_configured: false } });
    if (path === '/api/v1/meetings') return route.fulfill({ json: { items: [], total: 0, limit: 20, offset: 0 } });
    if (path === '/api/v1/assistant/chat') {
      plans++;
      expect(pendingSaves).toBe(1);
      await new Promise<void>((resolve) => { release = resolve });
      return route.fulfill({ json: { action: 'reply', answer: 'Ответ готов после перехода.', search_query: '' } });
    }
    throw new Error(`Unexpected request ${path}`);
  });
  await page.goto('/chat');
  await page.getByRole('textbox', { name: 'Сообщение ассистенту' }).fill('Вопрос с долгим ответом');
  await page.getByRole('button', { name: 'Отправить вопрос' }).click();
  await expect(page.getByRole('status')).toContainText('Готовлю ответ');
  const mainNav = page.getByRole('navigation', { name: 'Основная навигация' });
  await mainNav.getByRole('link', { name: 'Встречи', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Встречи', exact: true })).toBeVisible();
  await mainNav.getByRole('link', { name: 'Чат', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Вопрос с долгим ответом', exact: true })).toBeVisible();
  await expect(page.getByRole('status')).toContainText('Готовлю ответ');
  expect(plans).toBe(1);
  // Another conversation can be used while this one is still processing.
  await page.getByRole('button', { name: 'Новый чат', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Чем могу помочь?' })).toBeVisible();
  const saved = page.waitForResponse((r) => r.url().endsWith('/turns') && r.request().method() === 'POST');
  release!();
  await saved;
  await expect(page.getByRole('heading', { name: 'Чем могу помочь?' })).toBeVisible();
  await page.getByRole('navigation', { name: 'Список чатов' }).getByRole('button', { name: 'Вопрос с долгим ответом', exact: true }).click();
  await expect(page.getByText('Ответ готов после перехода.', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('Ответ готов после перехода.', { exact: true })).toBeVisible();
  await expect(page.locator('.workspace-chat-question')).toHaveCount(1);
  expect(plans).toBe(1);
});

test('reload resumes a persisted pending message without duplicating it', async ({ page }) => {
  const chatStore = conversationStore();
  const uid = randomUUID();
  let releaseOld: (() => void) | undefined;
  let plans = 0;
  const ids: string[] = [];
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/\/(assistant|rag)\/chat\/stream$/, '/$1/chat');
    if (path.startsWith('/api/v1/assistant/conversations')) {
      if (path.endsWith('/pending')) ids.push(route.request().postDataJSON().id);
      return chatStore(route);
    }
    if (path === '/api/v1/auth/me') return route.fulfill({ json: { id: uid, email: 'qa@example.com', display_name: 'QA' } });
    if (path === '/api/v1/rag/config') return route.fulfill({ json: { offline: true, llm_provider: 'ollama', llm_model: 'test', reasoning_effort: 'max', embedding_provider: 'ollama', embedding_model: 'test', embedding_dimensions: 3, cloud_configured: false } });
    if (path === '/api/v1/assistant/chat') {
      plans++;
      if (plans === 1) await new Promise<void>((resolve) => { releaseOld = resolve });
      try { await route.fulfill({ json: { action: 'reply', answer: 'Ответ восстановлен.', search_query: '' } }); }
      catch { /* The original request is cancelled by the page reload. */ }
      return;
    }
    throw new Error(`Unexpected request ${path}`);
  });
  await page.goto('/chat');
  await page.getByRole('textbox', { name: 'Сообщение ассистенту' }).fill('Вопрос перед перезагрузкой');
  await page.getByRole('button', { name: 'Отправить вопрос' }).click();
  await expect.poll(() => plans).toBe(1);
  await page.reload();
  await expect(page.getByText('Ответ восстановлен.', { exact: true })).toBeVisible();
  releaseOld!();
  expect(plans).toBe(2);
  expect(new Set(ids).size).toBe(1);
  await expect(page.locator('.workspace-chat-question')).toHaveCount(1);
});

test('streamed text is visible before completion and stream errors survive reload', async ({ page }) => {
  const chatStore = conversationStore();
  const uid = randomUUID();
  let completedSaves = 0;
  await page.addInitScript(() => {
    const nativeFetch = window.fetch.bind(window);
    const holder = window as unknown as { emitChatEvent?: (event: unknown) => void; chatStreamReady?: boolean };
    window.fetch = async (input, init) => {
      const url = new URL(typeof input === 'string' ? input : input instanceof Request ? input.url : input.toString(), location.href);
      if (url.pathname !== '/api/v1/assistant/chat/stream') return nativeFetch(input, init);
      const encoder = new TextEncoder();
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          holder.chatStreamReady = true;
          holder.emitChatEvent = (event) => {
            const bytes = encoder.encode('data: ' + JSON.stringify(event) + '\r\n\r\n');
            // Deliberately split UTF-8/SSE across byte chunks.
            controller.enqueue(bytes.slice(0, 37));
            controller.enqueue(bytes.slice(37, bytes.length - 3));
            controller.enqueue(bytes.slice(bytes.length - 3));
          };
        },
        cancel() { holder.chatStreamReady = false; holder.emitChatEvent = undefined; },
      });
      return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } });
    };
  });
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith('/api/v1/assistant/conversations')) {
      if (path.endsWith('/turns')) completedSaves++;
      return chatStore(route);
    }
    if (path === '/api/v1/auth/me') return route.fulfill({ json: { id: uid, email: 'qa@example.com', display_name: 'QA' } });
    if (path === '/api/v1/rag/config') return route.fulfill({ json: { offline: false, llm_provider: 'openai', llm_model: 'gpt-5.6-luna', reasoning_effort: 'max', embedding_provider: 'openai', embedding_model: 'test', embedding_dimensions: 3, cloud_configured: true } });
    if (path === '/api/v1/meetings') return route.fulfill({ json: { items: [], total: 0, limit: 20, offset: 0 } });
    throw new Error(`Unexpected request ${path}`);
  });
  const emit = (event: unknown) => page.evaluate((data) => (window as unknown as { emitChatEvent: (event: unknown) => void }).emitChatEvent(data), event);
  const streamReady = () => expect.poll(() => page.evaluate(() => (window as unknown as { chatStreamReady: boolean }).chatStreamReady)).toBe(true);
  await page.goto('/chat');
  const input = page.getByRole('textbox', { name: 'Сообщение ассистенту' });
  const send = page.getByRole('button', { name: 'Отправить вопрос' });
  await input.fill('Привет');
  await send.click();
  await streamReady();
  await emit({ type: 'delta', text: 'Привет' });
  await expect(page.locator('.workspace-assistant-text')).toHaveText('Привет');
  expect(completedSaves).toBe(0);
  const navigation = page.getByRole('navigation', { name: 'Основная навигация' });
  await navigation.getByRole('link', { name: 'Встречи', exact: true }).click();
  await navigation.getByRole('link', { name: 'Чат', exact: true }).click();
  await expect(page.locator('.workspace-assistant-text')).toHaveText('Привет');
  await emit({ type: 'delta', text: '! Чем помочь?' });
  await expect(page.locator('.workspace-assistant-text')).toHaveText('Привет! Чем помочь?');
  expect(completedSaves).toBe(0);
  await emit({ type: 'result', data: { action: 'reply', answer: 'Привет! Чем помочь?', search_query: '' } });
  await expect.poll(() => completedSaves).toBe(1);
  await expect(page.locator('.workspace-chat-thinking')).toHaveCount(0);
  await input.fill('Вопрос с обрывом');
  await send.click();
  await streamReady();
  await emit({ type: 'delta', text: 'Незавершённый ответ' });
  await emit({ type: 'error', code: 'MODEL_OUTPUT_LIMIT', status: 502 });
  await expect(page.getByRole('alert')).toContainText('Модели не хватило лимита');
  expect(completedSaves).toBe(1);
  await page.reload();
  await expect(page.getByRole('alert')).toContainText('Модели не хватило лимита');
  await expect(page.getByRole('heading', { name: 'Вопрос с обрывом', exact: true })).toBeVisible();
});
