import { t, useLocale, localizeMessage, formatDate } from '../i18n'
import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { ActionIcon, Alert, Anchor, Button, Loader, Textarea } from '@mantine/core'
import { ArrowRight, ArrowUp, CalendarDays, CheckCircle2, ListTodo, MessageSquare, Plus, Search } from 'lucide-react'
import { PageHeading, LoadingState } from '../components/ui'
import { conversationsApi, type ConversationDetail, type ConversationSummary } from '../lib/conversations'
import { conversationJobKey, mergeConversationTurn, runConversationTurn, type ConversationJob } from '../lib/conversation-runner'
import { sessionQuery } from '../lib/query'
import { assistantPanel } from '../lib/assistant-panel'
import { ApiError } from '../lib/api'
import { ragApi, ragError, type AssistantResult } from '../lib/rag'
import type { ChatMeetingSelection } from '../components/rag/chat-meeting-panel'
import '../components/rag/workspace-chat.css'

const ChatMeetingPanel = lazy(() => import('../components/rag/chat-meeting-panel').then((module) => ({ default: module.ChatMeetingPanel })))

const suggestions = () => [
  t("На какой встрече обсуждали бюджет?"),
  t("Как загрузить запись?"),
  t("Помоги составить повестку встречи"),
]

function AnswerContent({ result, onOpenMeeting }: { result: AssistantResult; onOpenMeeting: (selection: ChatMeetingSelection) => void }) {
  useLocale()
  if (result.mode === 'navigation') return <div className="workspace-chat-answer">
    <div className="workspace-chat-speaker"><MessageSquare size={17} aria-hidden="true" />Soyle</div>
    <p>{result.answer || (result.meetings.length ? t('Выберите встречу для открытия') : t('Здесь будут ваши встречи'))}</p>
    <div className="workspace-chat-citations">{result.meetings.map((meeting) => <button type="button" className="workspace-chat-board-source" key={meeting.id}
      onClick={() => onOpenMeeting({ meetingId: meeting.id, title: meeting.title, view: result.view, quotes: [] })}>
      <CalendarDays size={20} aria-hidden="true" /><span className="chat-source-copy"><strong>{meeting.title}</strong></span><ArrowRight size={17} aria-hidden="true" />
    </button>)}</div>
  </div>
  if (result.mode === 'assistant') return <div className="workspace-chat-answer">
    <div className="workspace-chat-speaker"><MessageSquare size={17} aria-hidden="true" />Soyle</div>
    <p className="workspace-assistant-text">{result.answer}</p>
  </div>
  if (result.mode === 'tasks') return <div className="workspace-chat-answer">
    <div className="workspace-chat-speaker"><MessageSquare size={17} aria-hidden="true" />Soyle</div>
    <div className="workspace-chat-operation is-complete"><CheckCircle2 size={20} aria-hidden="true" /><strong>{result.answer}</strong></div>
    <div className="workspace-chat-created-tasks">{result.tasks.map((task) => <button type="button" key={task.card_id} className="workspace-chat-created-task" onClick={() => onOpenMeeting({ meetingId: task.meeting_id, title: task.meeting_title, view: 'kanban', quotes: [] })}>
      <ListTodo size={19} aria-hidden="true" /><div><strong>{task.title}</strong><span>{task.meeting_title} {t("· К выполнению")}</span>
        {task.assignee && <span>{t("Ответственный:")} {task.assignee}</span>}
        {((task.due_date ? formatDate(task.due_date) : task.due_text)) && <span>{t("Срок:")} {(task.due_date ? formatDate(task.due_date) : task.due_text)}</span>}
      </div><ArrowRight size={17} aria-hidden="true" />
    </button>)}</div>
  </div>
  const sources = new Map(result.sources.map((source) => [source.source_id, source]))
  return <div className="workspace-chat-answer">
    <div className="workspace-chat-speaker"><MessageSquare size={17} aria-hidden="true" />Soyle</div>
    {result.status === 'insufficient_evidence'
      ? <p>{result.coverage.total === 0 ? t("В вашем рабочем пространстве пока нет встреч для поиска.") : result.coverage.ready === 0 ? t("В доступных встречах пока нет стенограмм для поиска.") : t("В найденных фрагментах недостаточно данных для ответа. Уточните тему, имя или формулировку вопроса.")}</p>
      : result.claims.map((claim, i) => <div className="workspace-chat-claim" key={i}>
        <p>{claim.text}</p>
        <div className="workspace-chat-citations">
          {claim.citations.map((citation, j) => {
            if ('kind' in citation && citation.kind === 'board') {
              const record = result.board_sources.find((item) => item.source_id === citation.source_id)
              if (!record) return null
              const related = claim.citations.flatMap((item, index) => {
                if (!('kind' in item) || item.kind !== 'board') return []
                const source = result.board_sources.find((source) => source.source_id === item.source_id)
                return source?.meeting_id === record.meeting_id && source.kind === record.kind ? [{ index, quote: item.quote, provisional: source.provisional }] : []
              })
              // One source card per meeting and view, preserving every supporting quote.
              if (related[0]?.index !== j) return null
              const quotes = [...new Set(related.map((item) => item.quote))]
              const provisional = related.some((item) => item.provisional)
              return <button type="button" className="workspace-chat-board-source" key={`${record.meeting_id}-${record.kind}`}
                onClick={() => onOpenMeeting({ meetingId: record.meeting_id, title: record.meeting_title, view: record.kind === 'kanban' ? 'kanban' : 'insights', quotes, provisional })}>
                <span className="chat-source-icon"><ListTodo size={20} aria-hidden="true" /></span>
                <span className="chat-source-copy"><span className="chat-source-kind">{record.kind === 'kanban' ? t("Канбан") : t("Итоги")}<span className="chat-source-count">{quotes.length}</span></span><strong>{record.meeting_title}</strong>
                  {provisional && <span className="chat-source-provisional">{t("Промежуточные итоги — ещё могут измениться.")}</span>}
                </span><ArrowRight size={17} aria-hidden="true" />
              </button>
            }
            if ('kind' in citation && citation.kind === 'catalog') {
              const record = result.catalog_sources.find((item) => item.source_id === citation.source_id)
              return record && <details className="workspace-chat-source" key={`${citation.source_id}-${j}`}>
                <summary><CalendarDays size={16} aria-hidden="true" /><span>{record.meeting_title || t("Список встреч")}</span><span className="workspace-chat-source-hint">{t("Источник")}</span></summary>
                <blockquote>{citation.quote}</blockquote>
                {record.meeting_id ? <Anchor renderRoot={(props) => <Link {...props} to="/meetings/$meetingId" params={{ meetingId: record.meeting_id! }} />}>{t("Открыть встречу")} <ArrowRight size={14} aria-hidden="true" /></Anchor>
                  : <Anchor renderRoot={(props) => <Link {...props} to="/meetings" search={{ q: '', offset: 0 }} />}>{t("Все встречи")} <ArrowRight size={14} aria-hidden="true" /></Anchor>}
              </details>
            }
            const source = sources.get(citation.source_id)
            return source && <details className="workspace-chat-source" key={`${citation.source_id}-${j}`}>
              <summary><CalendarDays size={16} aria-hidden="true" /><span>{source.meeting_title}</span><span className="workspace-chat-source-hint">{t("Цитата")}</span></summary>
              <blockquote>{citation.quote}</blockquote>
              <Anchor renderRoot={(props) => <Link {...props} to="/meetings/$meetingId" params={{ meetingId: source.meeting_id }} />}>{t("Открыть встречу")} <ArrowRight size={14} aria-hidden="true" /></Anchor>
            </details>
          })}
        </div>
      </div>)}
    {result.coverage.ready < result.coverage.total && <p className="workspace-chat-note">{t("Поиск выполнен по доступным стенограммам:")} {result.coverage.ready} {t("из")} {result.coverage.total}.</p>}
    {result.board_coverage.selected_sources < result.board_coverage.available_sources && <p className="workspace-chat-note">{t("Для ответа использована часть карточек и итогов. Уточните встречу или тему, чтобы сузить поиск.")}</p>}
  </div>
}

export function ChatPage() {
  useLocale()
  const cache = useQueryClient()
  const session = useQuery(sessionQuery)
  const listKey = ['assistant-chats', session.data?.id, 'list']
  const chats = useQuery({ queryKey: listKey, queryFn: ({ signal }) => conversationsApi.list(signal) })
  const selectionKey = ['assistant-selected-chat', session.data?.id]
  const selection = useQuery<string | null>({ queryKey: selectionKey, queryFn: () => null, enabled: false,
    initialData: () => { try { return sessionStorage.getItem(`soyle-chat:${session.data?.id}`) } catch { return null } }, gcTime: Infinity })
  const selected = selection.data
  const setSelected = (id: string) => {
    cache.setQueryData(selectionKey, id)
    try { sessionStorage.setItem(`soyle-chat:${session.data?.id}`, id) } catch { /* Optional selection persistence. */ }
  }
  const initialRequested = useRef(false)
  const create = useMutation({
    mutationFn: conversationsApi.create,
    onSuccess: (chat) => {
      cache.setQueryData<ConversationSummary[]>(listKey, (old = []) => [chat, ...old.filter((item) => item.id !== chat.id)])
      setSelected(chat.id)
    },
  })
  const createChat = create.mutate
  useEffect(() => {
    if (chats.data?.length === 0 && !initialRequested.current) {
      initialRequested.current = true
      createChat(crypto.randomUUID())
    }
  }, [chats.data, createChat])
  const active = chats.data?.find((chat) => chat.id === selected) ?? chats.data?.[0]
  useEffect(() => {
    if (active && active.id !== selected) {
      cache.setQueryData(['assistant-selected-chat', session.data?.id], active.id)
      try { sessionStorage.setItem(`soyle-chat:${session.data?.id}`, active.id) } catch { /* Optional. */ }
    }
  }, [active, selected, cache, session.data?.id])
  if (chats.isPending) return <LoadingState />
  if (chats.isError) return <Alert color="red" title={t("Не удалось загрузить чаты")}><Button onClick={() => void chats.refetch()}>{t("Повторить")}</Button></Alert>
  return <div className="workspace-chats">
    <aside className="assistant-conversations" aria-label={t("Ваши чаты")}>
      <Button leftSection={<Plus size={17} />} variant="light" loading={create.isPending} onClick={() => create.mutate(crypto.randomUUID())}>{t("Новый чат")}</Button>
      {create.isError && <Alert color="red">{t("Не удалось создать чат. Попробуйте ещё раз.")}</Alert>}
      <nav aria-label={t("Список чатов")}>{chats.data.map((chat) => <button type="button" key={chat.id} className={active?.id === chat.id ? 'is-active' : ''} aria-current={active?.id === chat.id ? 'page' : undefined} title={chat.title === "Новый чат" ? t("Новый чат") : chat.title} onClick={() => setSelected(chat.id)}><MessageSquare size={16} aria-hidden="true" /><span>{chat.title === "Новый чат" ? t("Новый чат") : chat.title}</span></button>)}</nav>
    </aside>
    {active && !create.isPending ? <ConversationPanel key={active.id} conversationId={active.id} title={active.title} /> : <LoadingState />}
  </div>
}

function ConversationPanel({ conversationId, title }: { conversationId: string; title: string }) {
  useLocale()

  const cache = useQueryClient()
  const session = useQuery(sessionQuery)
  const userId = session.data!.id
  const historyKey = ['assistant-chats', userId, conversationId]
  const jobKey = conversationJobKey(userId, conversationId)
  const job = useQuery<ConversationJob | null>({ queryKey: jobKey, queryFn: () => null, enabled: false, initialData: null, gcTime: Infinity })
  const history = useQuery({ queryKey: historyKey, staleTime: 0, queryFn: async ({ signal }) => {
    const detail = await conversationsApi.get(conversationId, signal)
    const pending = cache.getQueryData<ConversationJob | null>(jobKey)
    let merged = detail
    const cached = cache.getQueryData<typeof detail>(historyKey)
    for (const turn of cached?.turns ?? []) {
      if (turn.result.mode !== 'pending' && turn.result.mode !== 'failed') merged = mergeConversationTurn(merged, turn)
    }
    return pending ? mergeConversationTurn(merged, pending.turn) : merged
  } })
  const turns = history.data?.turns ?? []
  const [question, setQuestion] = useState('')
  const [openedMeeting, setOpenedMeeting] = useState<ChatMeetingSelection | null>(null)
  const awaitedPanels = useRef(new Set<string>())
  useEffect(() => {
    const key = ['assistant-chats', userId, conversationId]
    const existing = cache.getQueryData<ConversationDetail>(key)
    for (const turn of existing?.turns ?? []) {
      if (turn.result.mode === 'pending') awaitedPanels.current.add(turn.id)
    }
    // React only to newly completed turns, never replay UI actions from stored history.
    return cache.getQueryCache().subscribe((event) => {
      const queryKey = event.query.queryKey
      if (queryKey.length !== 3 || queryKey[0] !== key[0] || queryKey[1] !== userId || queryKey[2] !== conversationId) return
      const detail = event.query.state.data as ConversationDetail | undefined
      for (const turn of detail?.turns ?? []) {
        if (turn.result.mode === 'pending') { awaitedPanels.current.add(turn.id); continue }
        if (turn.result.mode === 'failed' || !awaitedPanels.current.delete(turn.id)) continue
        if (turn.result.mode !== 'meetings' && turn.result.mode !== 'tasks' && turn.result.mode !== 'navigation') continue
        const selection = assistantPanel(turn.result)
        if (selection) setOpenedMeeting(selection)
      }
    })
  }, [cache, userId, conversationId])
  const input = useRef<HTMLTextAreaElement>(null)
  const body = useRef<HTMLDivElement>(null)
  const following = useRef(true)
  const config = useQuery({ queryKey: ['rag', 'config'], queryFn: ({ signal }) => ragApi.config(signal) })
  const pending = turns.find((turn) => turn.result.mode === 'pending')
  const running = job.data?.state === 'running' || Boolean(pending)
  useEffect(() => {
    if (pending && !job.data && !history.isFetching) void runConversationTurn(userId, conversationId, pending.question, pending.id)
  }, [pending, job.data, history.isFetching, userId, conversationId])
  const partialText = pending?.result.mode === 'pending' ? pending.result.partial_text : undefined
  useEffect(() => {
    if (following.current && body.current) body.current.scrollTop = body.current.scrollHeight
  }, [turns.length, running, partialText])
  const ready = !config.isError && Boolean(config.data) && !history.isPending && !history.isError
  function submit() {
    if (ready && question.trim() && !running) {
      const value = question.trim()
      following.current = true
      setQuestion('')
      const turnId = crypto.randomUUID()
      awaitedPanels.current.add(turnId)
      void runConversationTurn(userId, conversationId, value, turnId)
    }
  }
  return <div className={`page workspace-chat ${turns.length || running ? 'has-conversation' : ''}`}>
    <header className="page-header workspace-chat-header">
      <div><PageHeading title={t("Чат")} /><p className="workspace-chat-subtitle">{title === 'Новый чат' ? t("Ваш ассистент") : title}</p></div>
    </header>
    <div className="workspace-chat-body" ref={body} onScroll={(event) => {
      const element = event.currentTarget
      following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 64
    }}>
    {history.isError && <Alert color="red" title={t("Не удалось загрузить диалог")}><Button onClick={() => void history.refetch()}>{t("Повторить")}</Button></Alert>}
    {config.isError && <Alert color="red" title={t("Ассистент недоступен")} role="alert">
      {ragError(config.error, 'workspace')}
      <Button variant="subtle" onClick={() => { void config.refetch() }}>{t("Повторить")}</Button>
    </Alert>}
    {turns.length === 0 && !running ? <section className="workspace-chat-welcome" aria-labelledby="chat-welcome">
      <h2 id="chat-welcome">{t("Чем могу помочь?")}</h2>
      <p>{t("Помогу разобраться в Soyle, обсудить идею или найти нужное в ваших встречах.")}</p>
      <div className="workspace-chat-suggestions">{suggestions().map((value) => <button type="button" key={value} onClick={() => { setQuestion(value); input.current?.focus() }}>
        <Search size={20} aria-hidden="true" /><span>{value}</span><ArrowRight size={18} aria-hidden="true" />
      </button>)}</div>
    </section> : <section className="workspace-chat-conversation" aria-label={t("Диалог с ассистентом")} aria-live="polite" aria-relevant="additions">
      {turns.map((turn) => <article className="workspace-chat-turn" key={turn.id}>
        <h2 className="workspace-chat-question">{turn.question}</h2>
        {turn.result.mode === 'pending' ? <><p className="workspace-assistant-text">{turn.result.partial_text}</p><div className={turn.result.activity === 'creating' ? 'workspace-chat-operation' : 'workspace-chat-thinking'} role="status"><Loader size="sm" />{turn.result.activity === 'creating' ? <div><strong>{t("Создаю задачи в канбане…")}</strong><p>{t("Проверяю встречу и добавляю карточки.")}</p></div> : t("Готовлю ответ…")}</div></>
          : turn.result.mode === 'failed' ? <Alert color="red" role="alert">
            {job.data?.turn.id === turn.id ? localizeMessage(job.data.error) : turn.result.error_code ? ragError(new ApiError(turn.result.error_status ?? 502, turn.result.error_code), 'workspace') : t("Не удалось завершить обработку сообщения.")}
            <Button variant="subtle" disabled={running} onClick={() => { awaitedPanels.current.add(turn.id); void runConversationTurn(userId, conversationId, turn.question, turn.id) }}>{t("Повторить отправку")}</Button>
          </Alert> : <AnswerContent result={turn.result as AssistantResult} onOpenMeeting={setOpenedMeeting} />}
      </article>)}
    </section>}
    </div>
    <div className="workspace-chat-composer-wrap">
      <form className="workspace-chat-composer" onSubmit={(event) => { event.preventDefault(); submit() }}>
        <Textarea ref={input} aria-label={t("Сообщение ассистенту")} placeholder={t("Напишите сообщение…")} value={question} maxLength={2000} autosize minRows={1} maxRows={6} disabled={running}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); submit() } }} />
        <ActionIcon type="submit" size={46} radius="xl" aria-label={t("Отправить вопрос")} disabled={!ready || !question.trim()} loading={running}><ArrowUp size={23} aria-hidden="true" /></ActionIcon>
      </form>
      <p className="workspace-chat-note">{config.isPending || history.isPending ? t("Подключаем ассистента…") : t("Общение, помощь с Soyle и поиск по встречам.")}</p>
    </div>
    {openedMeeting && <Suspense fallback={<div className="chat-panel-loading" role="status"><Loader size="sm" />{t('Загрузка')}</div>}>
      <ChatMeetingPanel key={`${openedMeeting.meetingId}-${openedMeeting.view}`} selection={openedMeeting} onClose={() => setOpenedMeeting(null)} />
    </Suspense>}
  </div>
}
