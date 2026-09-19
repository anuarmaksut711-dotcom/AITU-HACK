import { t } from '../i18n'
import { z } from 'zod'
import { ApiError } from './api'
import type { components } from './api.generated'

export const ragStatusSchema = z.object({
  index_id: z.uuid().nullable(),
  status: z.enum(['not_indexed', 'queued', 'running', 'ready', 'failed']),
  node_count: z.number().int(), error_code: z.string().nullable(),
}) satisfies z.ZodType<components['schemas']['IndexStatus']>
const sourceSchema = z.object({
  source_id: z.string(), node_id: z.uuid(), parent_id: z.uuid().nullable(),
  start_char: z.number().int(), end_char: z.number().int(), text: z.string(),
  reason: z.enum(['hit', 'parent', 'neighbor']),
})
const citationSchema = z.object({
  source_id: z.string(), node_id: z.uuid(), start_char: z.number().int(),
  end_char: z.number().int(), quote: z.string(),
})
const answerSchema = z.object({
  status: z.enum(['answered', 'insufficient_evidence']), answer: z.string(),
  claims: z.array(z.object({ text: z.string(), citations: z.array(citationSchema) })),
  sources: z.array(sourceSchema), index_id: z.uuid(), model: z.string(),
  provider: z.string(), prompt_version: z.string(),
}) satisfies z.ZodType<components['schemas']['Answer']>
const configSchema = z.object({
  offline: z.boolean(), llm_provider: z.string(), llm_model: z.string(), reasoning_effort: z.string(),
  embedding_provider: z.string(), embedding_model: z.string(), embedding_dimensions: z.number(),
  cloud_configured: z.boolean(),
}) satisfies z.ZodType<components['schemas']['RagConfiguration']>

export type RagAnswer = z.infer<typeof answerSchema>

const workspaceCoverageSchema = z.object({
  total: z.number().int().nonnegative(), ready: z.number().int().nonnegative(),
  pending: z.number().int().nonnegative(), failed: z.number().int().nonnegative(),
  not_indexed: z.number().int().nonnegative(), unavailable: z.number().int().nonnegative(),
}) satisfies z.ZodType<components['schemas']['WorkspaceCoverage']>
const panelActionSchema = z.object({ meeting_id: z.uuid(), view: z.enum(['kanban', 'insights', 'conversation']) }).nullable().default(null)
const workspaceAnswerSchema = z.object({
  panel: panelActionSchema,
  status: z.enum(['answered', 'insufficient_evidence']), answer: z.string(),
  claims: z.array(z.object({ text: z.string(), citations: z.array(z.union([
    citationSchema, z.object({ kind: z.literal('catalog'), source_id: z.string(), quote: z.string() }),
    z.object({ kind: z.literal('board'), source_id: z.string(), quote: z.string() }),
  ])) })),
  sources: z.array(sourceSchema.extend({
    meeting_id: z.uuid(), meeting_title: z.string(), meeting_created_at: z.string(),
  })),
  coverage: workspaceCoverageSchema,
  catalog_sources: z.array(z.object({ source_id: z.string(), text: z.string(), meeting_id: z.uuid().nullable(), meeting_title: z.string().nullable() })).default([]),
  board_sources: z.array(z.object({
    source_id: z.string(), kind: z.enum(['summary', 'kanban']), meeting_id: z.uuid(), meeting_title: z.string(),
    card_id: z.uuid().nullable(), title: z.string(), text: z.string(), board_version: z.number().int(),
    provisional: z.boolean(), transcript_current: z.boolean(), transcript_quote: z.string().nullable(),
  })).default([]),
  board_coverage: z.object({ available_sources: z.number().int(), selected_sources: z.number().int() }).default({ available_sources: 0, selected_sources: 0 }),
}) satisfies z.ZodType<components['schemas']['WorkspaceAnswer']>
export type WorkspaceAnswer = z.infer<typeof workspaceAnswerSchema>
export type WorkspaceCoverage = z.infer<typeof workspaceCoverageSchema>

export type ConversationMessage = { role: 'user' | 'assistant'; content: string }
const taskCreationSchema = z.object({
  panel: panelActionSchema,
  status: z.enum(['created', 'clarification']), answer: z.string(),
  tasks: z.array(z.object({ meeting_id: z.uuid(), meeting_title: z.string(), card_id: z.uuid(),
    board_version: z.number().int(), title: z.string(), description: z.string(),
    assignee: z.string().nullable(), due_date: z.string().nullable(), due_text: z.string().nullable() })),
}) satisfies z.ZodType<components['schemas']['TaskCreationResult']>
const navigationSchema = z.object({ mode: z.literal('navigation'), answer: z.string(), view: z.enum(['kanban', 'insights', 'conversation']), panel: panelActionSchema, meetings: z.array(z.object({ id: z.uuid(), title: z.string() })) })
export const assistantResultSchema = z.discriminatedUnion('mode', [
  z.object({ mode: z.literal('assistant'), answer: z.string() }),
  workspaceAnswerSchema.extend({ mode: z.literal('meetings') }),
  taskCreationSchema.extend({ mode: z.literal('tasks') }),
  navigationSchema,
])
export type AssistantResult = z.infer<typeof assistantResultSchema>

const assistantDecisionSchema = z.object({
  action: z.enum(['reply', 'search_meetings', 'create_tasks', 'open_panel']), answer: z.string(), search_query: z.string(),
}) satisfies z.ZodType<components['schemas']['AssistantDecision']>

export async function talkToAssistant(question: string, history: ConversationMessage[], signal: AbortSignal,
  operation: { id: string; conversationId: string; retryTask: boolean; onTaskStart: () => void | Promise<void>; onDelta?: (text: string) => void }): Promise<AssistantResult> {
  const decision = operation.retryTask ? { action: 'create_tasks' as const, answer: '', search_query: '' }
    : await streamRequest('/assistant/chat/stream', assistantDecisionSchema, { question, history }, signal, operation.onDelta)
  if (decision.action === 'reply') return { mode: 'assistant', answer: decision.answer }
  if (decision.action === 'open_panel') return request('/assistant/panel', navigationSchema, { question: decision.search_query || question, history }, signal)
  if (decision.action === 'create_tasks') {
    await operation.onTaskStart()
    const result = await request('/assistant/tasks', taskCreationSchema, { question, history, request_id: operation.id, conversation_id: operation.conversationId }, signal)
    return result.status === 'clarification' ? { mode: 'assistant', answer: result.answer } : { ...result, mode: 'tasks' }
  }
  const result = await searchWorkspace(decision.search_query, signal, () => {}, operation.onDelta)
  return { ...result, mode: 'meetings' }
}

export async function streamRequest<T>(path: string, schema: z.ZodType<T>, body: unknown, signal: AbortSignal, onDelta?: (text: string) => void): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { method: 'POST', credentials: 'include', cache: 'no-store', signal,
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'aimeet' }, body: JSON.stringify(body) })
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    const error = z.object({ error: z.object({ code: z.string() }) }).safeParse(data)
    throw new ApiError(response.status, error.success ? error.data.error.code : 'request')
  }
  if (!response.headers.get('content-type')?.includes('text/event-stream')) {
    const parsed = schema.safeParse(await response.json())
    if (!parsed.success) throw new ApiError(502, 'invalid-response')
    return parsed.data
  }
  const reader = response.body?.getReader()
  if (!reader) throw new ApiError(502, 'INCOMPLETE_MODEL_RESPONSE')
  const decoder = new TextDecoder()
  let buffer = '', bytes = 0
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      bytes += value.length
      if (bytes > 8_000_000) throw new ApiError(502, 'invalid-response')
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, '\n')
      let boundary: number
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const payload = block.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n')
        if (!payload) continue
        const event: unknown = JSON.parse(payload)
        const type = z.object({ type: z.string() }).parse(event).type
        if (type === 'delta') onDelta?.(z.object({ text: z.string() }).parse(event).text)
        else if (type === 'error') {
          const error = z.object({ code: z.string(), status: z.number() }).parse(event)
          throw new ApiError(error.status, error.code)
        } else if (type === 'result') return schema.parse(z.object({ data: z.unknown() }).parse(event).data)
      }
    }
    throw new ApiError(502, 'INCOMPLETE_MODEL_RESPONSE')
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock() }
}

async function request<T>(path: string, schema: z.ZodType<T>, question?: string | { question: string; history: ConversationMessage[]; request_id?: string; conversation_id?: string } | null, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method: question === undefined ? 'GET' : 'POST', credentials: 'include', cache: 'no-store', signal,
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'aimeet' },
    body: typeof question === 'string' ? JSON.stringify({ question }) : question ? JSON.stringify(question) : undefined,
  })
  if (!response.ok) {
    let code = 'request'
    try {
      const body: unknown = await response.json()
      const parsed = z.object({ error: z.object({ code: z.string() }) }).safeParse(body)
      if (parsed.success) code = parsed.data.error.code
    } catch { /* Keep a safe fallback. */ }
    throw new ApiError(response.status, code)
  }
  const parsed = schema.safeParse(await response.json())
  if (!parsed.success) throw new ApiError(502, 'invalid-response')
  return parsed.data
}
const path = (id: string) => `/meetings/${encodeURIComponent(id)}/rag`
export const ragApi = {
  workspaceStatus: (signal?: AbortSignal) => request('/rag/index', workspaceCoverageSchema, undefined, signal),
  indexWorkspace: (signal?: AbortSignal) => request('/rag/index', workspaceCoverageSchema, null, signal),
  askWorkspace: (question: string, signal?: AbortSignal) => request('/rag/chat', workspaceAnswerSchema, question, signal),
  config: (signal?: AbortSignal) => request('/rag/config', configSchema, undefined, signal),
  status: (id: string, signal?: AbortSignal) => request(`${path(id)}/index`, ragStatusSchema, undefined, signal),
  index: (id: string) => request(`${path(id)}/index`, ragStatusSchema, null),
  ask: (id: string, question: string) => request(`${path(id)}/chat`, answerSchema, question),
}

export async function searchWorkspace(question: string, signal: AbortSignal, onCoverage: (coverage: WorkspaceCoverage) => void, onDelta?: (text: string) => void) {
  let coverage = await ragApi.workspaceStatus(signal)
  onCoverage(coverage)
  if (coverage.not_indexed || coverage.failed) {
    coverage = await ragApi.indexWorkspace(signal)
    onCoverage(coverage)
  }
  // Preparing existing transcripts is part of search, not a separate user task.
  const deadline = Date.now() + 180_000
  while (coverage.pending > 0) {
    if (Date.now() >= deadline) throw new ApiError(504, 'SEARCH_PREPARING')
    await new Promise<void>((resolve, reject) => {
      signal.throwIfAborted()
      const abort = () => { clearTimeout(timer); reject(signal.reason) }
      const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, 1500)
      signal.addEventListener('abort', abort, { once: true })
    })
    coverage = await ragApi.workspaceStatus(signal)
    onCoverage(coverage)
  }
  if (!coverage.ready && coverage.failed) throw new ApiError(503, 'WORKSPACE_SEARCH_FAILED')
  return streamRequest('/rag/chat/stream', workspaceAnswerSchema, { question }, signal, onDelta)
}

export function ragError(error: unknown, scope: 'meeting' | 'workspace' = 'meeting'): string {
  if (error instanceof ApiError) {
    const messages: Record<string, string> = {
      OPENAI_KEY_REQUIRED: t("Для вопросов к встрече нужно настроить ключ OpenAI на сервере."),
      INDEX_NOT_READY: t("Сначала подготовьте встречу для вопросов."),
      WORKSPACE_INDEX_NOT_READY: t("Стенограммы ещё обрабатываются. Попробуйте отправить вопрос чуть позже."),
      WORKSPACE_SEARCH_FAILED: t("Не удалось обработать стенограммы для поиска. Попробуйте отправить вопрос ещё раз."),
      SEARCH_PREPARING: t("Стенограммы ещё обрабатываются. Попробуйте отправить вопрос чуть позже."),
      BOARD_CONFLICT: t("Канбан изменился. Повторите запрос — уже сохранённые задачи не будут созданы повторно."),
      BOARD_FULL: t("В канбане этой встречи достигнут лимит карточек."),
      INVALID_TASK_PLAN: t("Не удалось определить задачи или встречу. Уточните запрос."),
      OPERATION_CONFLICT: t("Запрос уже использовался с другими данными. Отправьте новое сообщение."),
      SOURCE_CHANGED: t("Одна из встреч изменилась во время поиска. Задайте вопрос ещё раз."),
      TRANSCRIPT_NOT_READY: t("Вопросы станут доступны после расшифровки записи."),
      MODEL_OUTPUT_LIMIT: t("Модели не хватило лимита для завершения ответа. Попробуйте уточнить вопрос."),
      INCOMPLETE_MODEL_RESPONSE: t("Модель не завершила ответ. Попробуйте ещё раз."),
      INVALID_MODEL_RESPONSE: t("Не удалось обработать ответ модели. Повторите запрос."),
      STREAM_FAILED: t("Поток ответа прервался. Повторите запрос."),
      CLIENT_ERROR: t("Не удалось обработать ответ. Повторите запрос."),
      PROVIDER_TIMEOUT: t("Модель не успела ответить. Попробуйте ещё раз."),
      PROVIDER_UNAVAILABLE: t("Не удалось подключиться к модели. Проверьте, что она запущена."),
      PROVIDER_RATE_LIMIT: t("Достигнут лимит запросов к модели. Попробуйте позже."),
      UNGROUNDED_MODEL_RESPONSE: t("Не удалось подтвердить ответ цитатами. Попробуйте уточнить вопрос."),
      PROVIDER_REJECTED: t("Модель отклонила запрос. Проверьте её настройки и доступ на сервере."),
    }
    if (messages[error.kind]) return messages[error.kind]
    if (error.status === 401) return t("Войдите в рабочее пространство ещё раз.")
    if (error.status === 404) return scope === 'workspace'
      ? t("Общий поиск пока недоступен на сервере.")
      : t("Встреча больше недоступна.")
  }
  return t("Не удалось получить ответ. Попробуйте ещё раз.")
}
