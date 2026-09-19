import { queryClient } from './query'
import { ApiError } from './api'
import { conversationsApi, type ConversationDetail, type ConversationTurn } from './conversations'
import { ragError, talkToAssistant, type ConversationMessage } from './rag'

export type ConversationJob = { state: 'running' | 'failed'; turn: ConversationTurn; error?: string }
export const conversationJobKey = (userId: string, conversationId: string) => ['assistant-chat-job', userId, conversationId]
const active = new Map<string, Promise<void>>()
export const unfinished = (turn: ConversationTurn) => turn.result.mode === 'pending' || turn.result.mode === 'failed'

export function mergeConversationTurn(detail: ConversationDetail, turn: ConversationTurn): ConversationDetail {
  const existing = detail.turns.find((item) => item.id === turn.id)
  // A pending UI snapshot must never hide an answer already committed by the server.
  if (existing && !unfinished(existing) && unfinished(turn)) return detail
  return { ...detail, turns: existing ? detail.turns.map((item) => item.id === turn.id ? turn : item) : [...detail.turns, turn] }
}

export function runConversationTurn(userId: string, conversationId: string, question: string, id: string = crypto.randomUUID()): Promise<void> {
  const key = `${userId}:${conversationId}`
  if (active.has(key)) return active.get(key)!
  const historyKey = ['assistant-chats', userId, conversationId]
  const jobKey = conversationJobKey(userId, conversationId)
  const controller = new AbortController()
  const sameUser = () => queryClient.getQueryData<{ id: string }>(['session'])?.id === userId
  if (!sameUser()) return Promise.resolve()
  const detail = queryClient.getQueryData<ConversationDetail>(historyKey)
  const existing = detail?.turns.find((turn) => turn.id === id)
  let current: ConversationTurn = existing ?? { id, question, result: { mode: 'pending', answer: '', activity: 'thinking', error_code: null, error_status: null }, created_at: new Date().toISOString() }
  let activity: 'thinking' | 'creating' = unfinished(current) && 'activity' in current.result ? current.result.activity : 'thinking'
  const prior = detail?.turns.slice(0, existing ? detail.turns.findIndex((turn) => turn.id === id) : undefined) ?? []
  const context: ConversationMessage[] = prior.filter((turn) => !unfinished(turn)).slice(-10).flatMap((turn) => [
    { role: 'user' as const, content: turn.question.slice(0, 6000) },
    { role: 'assistant' as const, content: (turn.result.mode === 'tasks' ? turn.result.answer + '\n' + turn.result.tasks.map((task) => `${task.title}; встреча ${task.meeting_title}; meeting_id=${task.meeting_id}`).join('\n') : turn.result.answer || 'В источниках не удалось найти подтверждение.').slice(0, 6000) },
  ])
  while (context.reduce((sum, message) => sum + message.content.length, 0) > 40_000) context.splice(0, 2)
  queryClient.setQueryDefaults(jobKey, { gcTime: Infinity })
  const publish = (turn: ConversationTurn, state: 'running' | 'failed', error?: string) => {
    if (!sameUser()) return
    current = turn
    queryClient.setQueryData<ConversationJob>(jobKey, { state, turn, error })
    queryClient.setQueryData<ConversationDetail>(historyKey, (old) => old ? mergeConversationTurn(old, turn) : old)
  }
  publish({ ...current, result: { mode: 'pending', answer: '', activity, error_code: null, error_status: null } }, 'running')
  // Navigation does not cancel the job; logout/account changes do.
  const unsubscribe = queryClient.getQueryCache().subscribe(() => { if (!sameUser()) controller.abort() })
  const promise = (async () => {
    try {
      const started = await conversationsApi.startTurn(conversationId, { id, question, state: 'pending', activity }, controller.signal)
      publish(started, 'running')
      void queryClient.invalidateQueries({ queryKey: ['assistant-chats', userId, 'list'] })
      if (!unfinished(started)) return
      if ('activity' in started.result) activity = started.result.activity
      let streamed = ''
      const result = await talkToAssistant(question, context, controller.signal, {
        onDelta: (delta) => {
          streamed += delta
          if (streamed.length > 48000) return
          if (sameUser() && unfinished(current)) publish({ ...current, result: { ...current.result, partial_text: streamed } } as ConversationTurn, 'running')
        },
        id, conversationId, retryTask: activity === 'creating',
        onTaskStart: async () => {
          activity = 'creating'
          const progress = await conversationsApi.startTurn(conversationId, { id, question, state: 'pending', activity }, controller.signal)
          publish(progress, 'running')
        },
      })
      const saved = await conversationsApi.saveTurn(conversationId, { id, question, result }, controller.signal)
      publish(saved, 'running')
      if (result.mode === 'tasks') for (const task of result.tasks) void queryClient.invalidateQueries({ queryKey: ['board', task.meeting_id] })
      void queryClient.invalidateQueries({ queryKey: ['assistant-chats', userId, 'list'] })
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) queryClient.setQueryData(['session'], null)
      if (!sameUser() || controller.signal.aborted) return
      const error_code = error instanceof ApiError ? error.kind : 'CLIENT_ERROR'
      const error_status = error instanceof ApiError ? error.status : 500
      publish({ ...current, result: { mode: 'failed', answer: '', activity, error_code, error_status } }, 'running', ragError(error, 'workspace'))
      try {
        const saved = await conversationsApi.startTurn(conversationId, { id, question, state: 'failed', activity, error_code, error_status }, controller.signal)
        publish(saved, unfinished(saved) ? 'failed' : 'running', unfinished(saved) ? ragError(error, 'workspace') : undefined)
      } catch { publish(current, 'failed', ragError(error, 'workspace')) }
    } finally {
      unsubscribe()
      active.delete(key)
      if (sameUser() && !unfinished(current)) queryClient.setQueryData(jobKey, null)
    }
  })()
  active.set(key, promise)
  return promise
}
