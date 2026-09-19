import { t, useLocale, localizeMessage, getLocale, formatDate, formatNumber } from '../../i18n'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { DatePickerInput } from '@mantine/dates'
import { useMediaQuery } from '@mantine/hooks'
import dayjs from 'dayjs'
import 'dayjs/locale/ru'
import 'dayjs/locale/kk'
import 'dayjs/locale/en'
import { DndContext, DragOverlay, KeyboardSensor, PointerSensor, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core'
import { BoardColumn, DraggableCard } from './board-dnd'
import { boardCollision, boardKeyboardCoordinates } from './board-dnd-geometry'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Controller, useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { Accordion, Alert, Avatar, Badge, Button, Checkbox, Flex, Menu, Modal, Popover, Progress, SegmentedControl, Select, Text, Textarea, TextInput, Title } from '@mantine/core'
import { Archive, CalendarDays, CircleHelp, Download, Plus, Search, SlidersHorizontal, Sparkles } from 'lucide-react'
import { ApiError } from '../../lib/api'
import { boardApi, boardError, cardInputSchema, emptyCard, agreements, clarificationLabels, kinds, overdue, priorities, statuses, type Board, type Card, type CardInput, type Evidence } from '../../lib/board'
import type { MeetingDetail } from '../../lib/contracts'
import { EvidenceButton, MeetingEvidence } from './meeting-evidence'
import { Disclosure } from '../ui'
import './meeting-board.css'

const options = (record: Record<string, string>) => Object.entries(record).map(([value, label]) => ({ value, label }))
const taskStatuses = ['todo', 'doing', 'blocked', 'done'] as const
const kindKeys = ['task', 'decision', 'topic', 'question', 'risk'] as const

export function MeetingBoard({ meetingId, title, canGenerate, meeting, embedded = false }: { meetingId: string; title: string; canGenerate: boolean; meeting: MeetingDetail; embedded?: boolean }) {
  useLocale()
  const cache = useQueryClient()
  const key = ['board', meetingId]
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) => boardApi.get(meetingId, signal),
    refetchInterval: (q) => ['queued', 'running'].includes(q.state.data?.status ?? '') ? 2000 : false })
  const [selectedView, setView] = useState<string | null>(null)
  const [columnOrder, setColumnOrder] = useState<Card['kind'][] | null>(null)
  const [search, setSearch] = useState('')
  const [assignee, setAssignee] = useState<string | null>(null)
  const [priority, setPriority] = useState<string | null>(null)
  const [needsReview, setNeedsReview] = useState(false)
  const [needsClarification, setNeedsClarification] = useState(false)
  const [evidence, setEvidence] = useState<Evidence | null>(null)
  const [pendingExport, setPendingExport] = useState<'csv' | 'json' | 'pdf' | null>(null)
  const [lateOnly, setLateOnly] = useState(false)
  const [showArchive, setShowArchive] = useState(false)
  const [editing, setEditing] = useState<{ card?: Card; version: number } | null>(null)
  const [dragged, setDragged] = useState<Card | null>(null)
  const dragStart = useRef<{ version: number; view: string; keyboard: boolean } | null>(null)
  const boardRoot = useRef<HTMLElement>(null)
  const focusAfterSave = useRef<string | null>(null)
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }), useSensor(KeyboardSensor, { coordinateGetter: boardKeyboardCoordinates }))
  const generation = useMutation({ mutationFn: () => boardApi.generate(meetingId), onSuccess: (data) => cache.setQueryData(key, data) })
  const save = useMutation({
    mutationFn: ({ card, changes, version }: { card: Card; changes: Partial<CardInput>; version?: number }) => boardApi.save(
      meetingId, version ?? query.data!.version, { ...cardInputSchema.parse(card), ...changes }, card.id),
    onMutate: async ({ card, changes }) => {
      await cache.cancelQueries({ queryKey: key })
      const previous = cache.getQueryData<Board>(key)
      if (previous) cache.setQueryData<Board>(key, { ...previous, cards: previous.cards.map((item) => item.id === card.id ? { ...item, ...changes } : item) })
      return { previous }
    },
    onSuccess: (data) => cache.setQueryData(key, data),
    onError: (_error, _variables, context) => {
      if (context?.previous) cache.setQueryData(key, context.previous)
      void cache.invalidateQueries({ queryKey: key })
    },
  })
  const download = useMutation({ mutationFn: (format: 'csv' | 'json' | 'ics') => boardApi.download(meetingId, format) })
  const board = query.data
  useEffect(() => {
    if (save.isPending || !focusAfterSave.current) return
    const handle = boardRoot.current?.querySelector<HTMLButtonElement>(`[data-card-id="${focusAfterSave.current}"] .card-drag-handle`)
    if (handle && !handle.disabled) {
      handle.focus({ preventScroll: true })
      focusAfterSave.current = null
    }
  }, [save.isPending, board])
  const busy = generation.isPending || ['queued', 'running'].includes(board?.status ?? '')
  const cards = board?.cards ?? []
  const view = selectedView ?? (embedded && !cards.some((card) => card.kind === 'task') ? 'content' : 'tasks')
  const contentKinds = columnOrder ?? (embedded ? [...kindKeys].sort((a, b) =>
    Number(cards.some((card) => card.kind === b && card.status !== 'dismissed')) - Number(cards.some((card) => card.kind === a && card.status !== 'dismissed'))) : [...kindKeys])
  const activeTasks = cards.filter((c) => c.kind === 'task' && c.status !== 'dismissed')
  const completed = activeTasks.filter((c) => c.status === 'done').length
  const unresolved = cards.filter((c) => c.status !== 'dismissed' && c.clarifications.length > 0)
  const visible = cards.filter((card) => (showArchive ? card.status === 'dismissed' : card.status !== 'dismissed')
    && (!search || [card.title, card.description, card.assignee, card.due_text].filter(Boolean).join(' ').toLocaleLowerCase().includes(search.toLocaleLowerCase()))
    && (!assignee || (assignee === '__none' ? !card.assignee : card.assignee === assignee))
    && (!priority || card.priority === priority) && (!needsReview || !card.reviewed) && (!lateOnly || overdue(card))
    && (!needsClarification || card.clarifications.length > 0))
  function drop(event: DragEndEvent) {
    const start = dragStart.current
    dragStart.current = null
    setDragged(null)
    if (!event.over || !start || start.view !== view || save.isPending || showArchive) return
    const card = cards.find((item) => item.id === event.active.id)
    const column = String(event.over.id).replace('column:', '')
    if (!card || !(view === 'tasks' ? [...taskStatuses] as string[] : [...kindKeys]).includes(column)) return
    const changes: Partial<CardInput> = view === 'tasks'
      ? { status: column as Card['status'] }
      : { kind: column as Card['kind'] }
    if (view === 'tasks' ? card.status === column : card.kind === column) return
    if (start.keyboard) focusAfterSave.current = card.id
    save.mutate({ card, changes, version: start.version })
  }
  function move(card: Card, status: Card['status']) { if (!save.isPending && card.status !== status) save.mutate({ card, changes: { status } }) }
  const canExportCalendar = cards.some((c) => c.kind === 'task' && c.reviewed && c.agreement === 'confirmed' && c.due_date && !['done', 'dismissed'].includes(c.status))
  const hasFilters = !!(search || assignee || priority || needsReview || lateOnly || needsClarification)
  function exportNow(format: 'csv' | 'json' | 'pdf') {
    if (format === 'pdf') window.print()
    else download.mutate(format)
  }
  function requestExport(format: 'csv' | 'json' | 'pdf') {
    if (unresolved.length || cards.some((c) => !c.reviewed && c.status !== 'dismissed')) setPendingExport(format)
    else exportNow(format)
  }
  function showClarifications() {
    setView('content'); setSearch(''); setAssignee(null); setPriority(null); setLateOnly(false)
    setShowArchive(false); setNeedsReview(false); setNeedsClarification(true)
  }
  return <section ref={boardRoot} className={`meeting-board transcript-panel ${embedded ? 'embedded-board' : ''}`} aria-labelledby="meeting-board-title">
    <header className={embedded ? 'board-heading-hidden' : 'board-heading'}>
      <Title order={2} id="meeting-board-title" size="h2">{embedded ? t("Канбан встречи") : t("Итоги встречи")}</Title>
    </header>
    <div className="board-toolbar no-print" role="group" aria-label={t("Управление канбаном")}>
      {embedded ? <Select className="board-grouping" size="sm" aria-label={t("Группировка карточек")} value={view} disabled={!!dragged || save.isPending}
        onChange={(value) => { if (value) { setView(value); setColumnOrder(null) } }} data={[{ value: 'tasks', label: t("По статусам поручений") }, { value: 'content', label: t("По содержанию встречи") }]} />
        : <SegmentedControl size="xs" value={view} onChange={setView} disabled={!!dragged || save.isPending} aria-label={t("Представление итогов")} data={[
          { value: 'tasks', label: t("Поручения") }, { value: 'content', label: t("По содержанию") }, { value: 'summary', label: t("Выжимка") },
        ]} />}
      <TextInput className="board-search" size="sm" aria-label={t("Поиск по карточкам")} placeholder={t("Найти карточку")} leftSection={<Search size={15} />} value={search} disabled={!!dragged} onChange={(e) => setSearch(e.currentTarget.value)} />
      <Popover position="bottom-end" width={290} trapFocus returnFocus withinPortal>
        <Popover.Target><Button size="sm" variant="default" disabled={!!dragged} leftSection={<SlidersHorizontal size={15} />}>{t("Фильтры")}{hasFilters || showArchive ? ' •' : ''}</Button></Popover.Target>
        <Popover.Dropdown className="board-filter-popover"><div className="board-filter-fields">
          <Select size="sm" comboboxProps={{ withinPortal: false }} label={t("Ответственный")} placeholder={t("Все ответственные")} clearable searchable value={assignee} onChange={setAssignee}
            data={[{ value: '__none', label: t("Без ответственного") }, ...Array.from(new Set(cards.flatMap((c) => c.assignee ? [c.assignee] : []))).map((name) => ({ value: name, label: name }))]} />
          <Select size="sm" comboboxProps={{ withinPortal: false }} label={t("Приоритет")} placeholder={t("Все приоритеты")} clearable value={priority} onChange={setPriority} data={options(priorities)} />
          <Checkbox label={t("Нужно проверить")} checked={needsReview} onChange={(e) => setNeedsReview(e.currentTarget.checked)} />
          <Checkbox label={t("Есть уточнения")} checked={needsClarification} onChange={(e) => setNeedsClarification(e.currentTarget.checked)} />
          <Checkbox label={t("Просрочено")} checked={lateOnly} onChange={(e) => setLateOnly(e.currentTarget.checked)} />
          <Checkbox label={t("Архив")} checked={showArchive} onChange={(e) => setShowArchive(e.currentTarget.checked)} />
          <Button size="sm" variant="default" disabled={!hasFilters && !showArchive} onClick={() => { setSearch(''); setAssignee(null); setPriority(null); setNeedsReview(false); setLateOnly(false); setNeedsClarification(false); setShowArchive(false) }}>{t("Сбросить фильтры")}</Button>
        </div></Popover.Dropdown>
      </Popover>
      <Button size="sm" variant="default" leftSection={<Plus size={15} />} disabled={!board || query.isError || save.isPending || !!dragged} onClick={() => setEditing({ version: board!.version })}>{t("Добавить карточку")}</Button>
      {board && (embedded ? board.status === 'failed' : board.status !== 'ready') && <Button size="sm" leftSection={<Sparkles size={15} />} loading={busy}
        disabled={!canGenerate || query.isError || !!dragged} onClick={() => generation.mutate()}>{busy ? t("Разбираем…") : board.status === 'failed' ? t("Повторить разбор") : t("Разобрать встречу")}</Button>}
        <Menu position="bottom-end" withinPortal><Menu.Target>
          <Button size="sm" variant="default" leftSection={<Download size={16} />} disabled={query.isError || download.isPending || (!cards.length && !board?.summary.length)}>{t("Экспорт")}</Button>
        </Menu.Target><Menu.Dropdown>
          <Menu.Label>{t("Вся доска, без фильтров")}</Menu.Label>
          <Menu.Item disabled={download.isPending} onClick={() => requestExport('csv')}>{t("Скачать CSV")}</Menu.Item>
          <Menu.Item disabled={download.isPending} onClick={() => requestExport('json')}>{t("Скачать JSON")}</Menu.Item>
          <Menu.Item onClick={() => requestExport('pdf')}>{t("Печать / PDF")}</Menu.Item>
          <Menu.Divider /><Menu.Label>{t("Подтверждённые поручения с датой")}</Menu.Label>
          <Menu.Item disabled={download.isPending || !canExportCalendar} onClick={() => download.mutate('ics')}>{t("Календарь .ics")}</Menu.Item>
        </Menu.Dropdown></Menu>
    </div>
    {query.isPending && <Text role="status">{t("Загружаем итоги встречи…")}</Text>}
    {query.isError && <Alert color="red" role="alert">{boardError(query.error)} <Button variant="subtle" onClick={() => void query.refetch()}>{t("Повторить загрузку")}</Button></Alert>}
    {!query.isError && board && <>
      {!embedded && board.status === 'idle' && !cards.length && <div className="board-intro no-print">
        <Sparkles size={24} aria-hidden="true" /><div><Text fw={600}>{t("Стенограмма станет рабочей доской")}</Text>
          <Text size="sm" c="dimmed">{t("Разберите встречу локальной моделью или добавьте карточки вручную. Неозвученные исполнители и сроки останутся пустыми.")}</Text>
          {!canGenerate && <Text size="sm">{t("Автоматический разбор станет доступен после подготовки стенограммы.")}</Text>}</div></div>}
      {busy && <div role="status" className="board-progress"><Text size="sm">{t("Выделяем решения и поручения. Можно уйти со страницы — обработка продолжится.")}</Text><Progress value={board.progress} animated aria-label={t("Обработка встречи")} /></div>}
      {board.status === 'failed' && <Alert color="red" role="alert">{boardError(board.error_code)}</Alert>}
      {generation.isError && <Alert color="red" role="alert">{boardError(generation.error)}</Alert>}
      {save.isError && <Alert color="red" role="alert">{boardError(save.error)}</Alert>}
      {!!activeTasks.length && <Text size="sm" c="dimmed" className="board-tally no-print">{t("Выполнено поручений: {completed} из {total}", { completed: formatNumber(completed), total: formatNumber(activeTasks.length) })}{cards.some(overdue) && <span className="card-overdue"> {t("· Просрочено:")} {cards.filter(overdue).length}</span>}
      </Text>}
      {!!unresolved.length && <div className="board-clarification-summary no-print">
        <Text size="sm">{t("В карточках остались вопросы:")} {unresolved.length}</Text>
        <Button variant="subtle" size="compact-sm" onClick={showClarifications}>{t("Перейти к уточнениям")}</Button>
      </div>}
      {download.isError && <Alert color="red" role="alert">{boardError(download.error)}</Alert>}
      {save.isPending && <Text size="xs" c="dimmed" role="status">{t("Сохраняем карточку…")}</Text>}
      {view === 'summary' ? <div className="board-summary screen-only">
        <Title order={3} size="h4">{t("Кратко о встрече")}</Title>
        {!board.summary.length ? <Text c="dimmed">{t("Выжимка появится после разбора содержательной стенограммы.")}</Text> : board.summary.map((sentence, i) => <div key={i}><Text>{sentence.text}</Text><Disclosure label={t("Основание {0}", { "0": i + 1 })}><blockquote>{sentence.quote}</blockquote>{sentence.evidence && <EvidenceButton evidence={sentence.evidence} onOpen={setEvidence} />}</Disclosure></div>)}
      </div> : <>
        {!showArchive && <Text size="xs" c="dimmed" className="no-print">{view === 'tasks' ? t("Перенос за ручку меняет статус поручения.") : t("Перенос за ручку меняет тип карточки.")}</Text>}
        <DndContext id={`board-dnd-${meetingId}`} sensors={sensors} collisionDetection={boardCollision}
          onDragStart={({ active, activatorEvent }) => { const card = cards.find((c) => c.id === active.id); if (card && board) { setView(view); setColumnOrder(contentKinds); setDragged(card); dragStart.current = { view, version: board.version, keyboard: activatorEvent.type === 'keydown' }; save.reset() } }}
          onDragCancel={() => { setDragged(null); dragStart.current = null }} onDragEnd={drop}
          accessibility={{ screenReaderInstructions: { draggable: t("Нажмите пробел, чтобы поднять карточку. Стрелками выберите колонку. Пробел — переместить, Escape — отменить.") }, announcements: {
            onDragStart: ({ active }) => t("Выбрана карточка «{0}». Стрелками выберите колонку.", { "0": active.data.current?.title }),
            onDragOver: ({ over }) => over ? t("Колонка «{0}».", { "0": over.data.current?.label }) : t("За пределами колонок."),
            onDragEnd: ({ over }) => over ? t("Карточка отпущена в колонке «{0}».", { "0": over.data.current?.label }) : t("Перенос отменён."),
            onDragCancel: () => t("Перенос отменён."),
          } }}>
        <div className={`kanban-grid screen-only ${view === 'content' ? 'content-grid' : ''}`}>
          {(showArchive ? ['dismissed'] : view === 'tasks' ? [...taskStatuses] : contentKinds).map((column, index) => {
            const rows = visible.filter((c) => showArchive ? (view === 'content' || c.kind === 'task') : view === 'tasks' ? c.kind === 'task' && c.status === column : c.kind === column)
            const label = showArchive ? t("Архив карточек") : view === 'tasks' ? statuses[column as Card['status']] : kinds[column as Card['kind']]
            return <BoardColumn key={column} id={`column:${column}`} index={index} label={label} className={`kanban-column column-${column}`} disabled={save.isPending || showArchive}>
              <header><span className="column-dot" /><Title order={3} size="sm">{label}</Title><span className="column-count">{rows.length}</span></header>
              {!rows.length && <div className="column-empty">{hasFilters ? t("Нет совпадений") : t("Пока нет карточек")}</div>}
              {rows.map((card) => <DraggableCard key={card.id} card={card} columnId={`column:${column}`} disabled={save.isPending || showArchive}>
                {(handle) => <>
                <div className="card-badges">{card.priority !== 'unspecified' && <Badge variant="light" color={card.priority === 'high' ? 'red' : 'gray'} size="sm">{priorities[card.priority]}</Badge>}
                  {['task', 'decision'].includes(card.kind) && <Badge variant="light" color={card.agreement === 'confirmed' ? 'forest' : 'orange'} c={card.agreement === 'confirmed' ? undefined : '#8a3511'} size="sm">{agreements[card.agreement]}</Badge>}
                  {!showArchive && handle}</div>
                <button className="card-title" onClick={() => { save.reset(); setEditing({ card, version: board.version }) }}>{card.title}</button>
                {card.description && <Text size="sm" c="dimmed" className="card-description">{card.description}</Text>}
                <div className="card-meta"><span className="card-person"><Avatar size={30} radius="xl" color="forest" aria-hidden="true">{card.assignee?.slice(0, 1).toLocaleUpperCase() || '?'}</Avatar>{card.assignee || t("Ответственный не указан")}</span>
                  <span className={`card-date ${overdue(card) ? 'card-overdue' : ''}`}><CalendarDays size={17} aria-hidden="true" />{card.due_date ? `${overdue(card) ? t("Просрочено: ") : t("Срок: ")}${formatDate(card.due_date)}` : card.due_text ? t("Срок: {0}", { "0": card.due_text }) : t("Срок не указан")}</span></div>
                <div className="card-review"><Text size="xs" c="dimmed">{card.origin === 'ai' ? t("Черновик ИИ") : t("Добавлено вручную")}{card.reviewed ? t(" · Проверено вами") : t(" · Нужно проверить")}</Text></div>
                {card.quote && <Disclosure label={t("Цитата из встречи")}><blockquote>{card.quote}</blockquote>{card.evidence && <EvidenceButton evidence={card.evidence} onOpen={setEvidence} />}</Disclosure>}
                {!!card.revisions.length && <Disclosure label={t("Изменения в разговоре · {0}", { "0": card.revisions.length })}><CardRevisions card={card} onOpen={setEvidence} /></Disclosure>}
                {!!card.clarifications.length && <Button variant="subtle" color="orange" c="#8a3511" size="compact-xs" onClick={() => setEditing({ card, version: board.version })}>{t("Уточнить:")} {card.clarifications.length}</Button>}
                {card.kind === 'task' && <Select aria-label={t("Статус: {0}", { "0": card.title })} size="xs" value={card.status} data={options(statuses)} disabled={save.isPending}
                  onChange={(value) => { if (value) move(card, value as Card['status']) }} />}
                <Flex gap="xs" wrap="wrap"><Button variant="subtle" size="compact-xs" onClick={() => setEditing({ card, version: board.version })}>{t("Открыть")}</Button>
                  {!card.reviewed && <Button variant="subtle" size="compact-xs" disabled={save.isPending} onClick={() => save.mutate({ card, changes: { reviewed: true } })}>{t("Проверено")}</Button>}
                  {card.status !== 'dismissed' && <Button variant="subtle" color="gray" size="compact-xs" aria-label={t("В архив: {0}", { "0": card.title })} disabled={save.isPending} onClick={() => move(card, 'dismissed')}><Archive size={13} /></Button>}</Flex>
              </>}</DraggableCard>)}
            </BoardColumn>
          })}
        </div>
        {createPortal(<DragOverlay dropAnimation={null}>{dragged && <div className="kanban-drag-preview" aria-hidden="true"><strong>{dragged.title}</strong><span>{dragged.assignee || t("Ответственный не указан")}</span></div>}</DragOverlay>, document.body)}
        </DndContext>
      </>}
      <div className="board-print"><h1>{title}</h1><h2>{t("Итоги встречи")}</h2>
        {board.summary.map((s, i) => <p key={i}>{s.text}</p>)}
        {kindKeys.map((kind) => <section key={kind}><h2>{kinds[kind]}</h2>{cards.filter((c) => c.kind === kind).map((c) => <article key={c.id}><h3>{c.title}</h3><p>{c.description}</p><p>{c.assignee || t("Ответственный не указан")} · {(c.due_date ? formatDate(c.due_date) : c.due_text) || t("Срок не указан")} · {priorities[c.priority]} · {statuses[c.status]} · {agreements[c.agreement]} · {c.reviewed ? t("Проверено пользователем") : t("Не проверено")}</p>{c.quote && <blockquote>{c.quote}</blockquote>}
          {!!c.clarifications.length && <p>{t("Уточнения:")} {c.clarifications.map((code) => clarificationLabels[code] ?? t("Проверьте карточку")).join('; ')}</p>}
          {c.revisions.map((revision, i) => <div key={i}><p>{revisionLabels[revision.field]}: {revision.before_value} → {revision.after_value}</p><blockquote>{revision.before.quote}</blockquote><blockquote>{revision.after.quote}</blockquote></div>)}
        </article>)}</section>)}
      </div>
    </>}
    {editing && <CardEditor meetingId={meetingId} initial={editing.card} version={editing.version} onEvidence={setEvidence}
      onClose={() => setEditing(null)} onSaved={(data) => { cache.setQueryData(key, data); setEditing(null) }} onRefresh={() => query.refetch()} />}
    {evidence && <MeetingEvidence key={`${evidence.start_char}:${evidence.end_char}`} meeting={meeting} evidence={evidence} onClose={() => setEvidence(null)} />}
    <Modal opened={pendingExport !== null} onClose={() => setPendingExport(null)} title={t("Проверить перед экспортом")} centered closeButtonProps={{ 'aria-label': t("Закрыть проверку экспорта") }}>
      <Text size="sm">{t("В протоколе есть непроверенные карточки или незаполненные договорённости. Можно уточнить их сейчас или сохранить документ с текущими пометками.")}</Text>
      <Flex mt="lg" gap="sm" wrap="wrap"><Button variant="default" onClick={() => { setPendingExport(null); showClarifications(); if (!unresolved.length) { setNeedsClarification(false); setNeedsReview(true) } }}>{t("Уточнить карточки")}</Button>
        <Button onClick={() => { const format = pendingExport; setPendingExport(null); if (format) setTimeout(() => exportNow(format), 250) }}>{t("Экспортировать с пометками")}</Button></Flex>
    </Modal>
  </section>
}

const revisionLabels = { get due_text() { return t("Срок") }, get assignee() { return t("Ответственный") }, get decision() { return t("Решение") } }
function CardRevisions({ card, onOpen }: { card: Card; onOpen: (evidence: Evidence) => void }) {
  useLocale()
  return <ol className="card-revisions">{card.revisions.map((revision, i) => <li key={i}>
    <Text size="sm" fw={600}>{revisionLabels[revision.field]}: {revision.before_value} → {revision.after_value}</Text>
    <blockquote>{revision.before.quote}</blockquote><EvidenceButton label={t("До изменения")} evidence={revision.before} onOpen={onOpen} />
    <blockquote>{revision.after.quote}</blockquote><EvidenceButton label={t("После изменения")} evidence={revision.after} onOpen={onOpen} />
  </li>)}</ol>
}

function CardEditor({ meetingId, initial, version, onClose, onSaved, onRefresh, onEvidence }: {
  meetingId: string; initial?: Card; version: number; onClose: () => void
  onSaved: (board: Awaited<ReturnType<typeof boardApi.get>>) => void
  onRefresh: () => Promise<unknown>
  onEvidence: (evidence: Evidence) => void
}) {
  useLocale()
  const fieldAppearance = { size: 'sm', variant: 'filled', radius: 'md' } as const
  const mobile = useMediaQuery('(max-width: 700px)')
  const form = useForm<CardInput>({ resolver: zodResolver(cardInputSchema), defaultValues: initial ? cardInputSchema.parse(initial) : emptyCard })
  const mutation = useMutation({ mutationFn: (values: CardInput) => boardApi.save(meetingId, version, values, initial?.id), onSuccess: onSaved })
  const conflict = mutation.error instanceof ApiError && mutation.error.kind === 'BOARD_CONFLICT'
  const select = (name: 'kind' | 'priority' | 'status' | 'agreement', label: string, values: Record<string, string>) => <Controller name={name} control={form.control} render={({ field }) => <Select {...fieldAppearance} label={label} data={options(values)} value={field.value} onChange={field.onChange} onBlur={field.onBlur} error={localizeMessage(form.formState.errors[name]?.message)} />} />
  return <Modal opened onClose={() => { if (!mutation.isPending) onClose() }} title={initial ? t("Карточка встречи") : t("Новая карточка")}
    size={1040} xOffset={16} yOffset={16} padding={0} centered
    classNames={{ content: 'card-editor-modal', header: 'card-editor-modal-header', body: 'card-editor-modal-body' }}
    closeOnClickOutside={false} closeOnEscape={!mutation.isPending} withCloseButton={!mutation.isPending} closeButtonProps={{ 'aria-label': t("Закрыть карточку") }}>
    <form className="card-editor card-editor-wide" onSubmit={form.handleSubmit((values) => mutation.mutate(values))}>
      <div className="card-editor-scroll">
        {!!initial?.clarifications.length && <Accordion variant="contained" radius="md" className="card-editor-clarifications">
          <Accordion.Item value="clarifications"><Accordion.Control icon={<CircleHelp size={17} aria-hidden="true" />}>{t("Нужно уточнить ·")} {initial.clarifications.length}</Accordion.Control>
            <Accordion.Panel><ul className="card-clarifications">{initial.clarifications.map((code) => <li key={code}>{clarificationLabels[code] ?? t("Проверьте карточку")}</li>)}</ul></Accordion.Panel>
          </Accordion.Item>
        </Accordion>}
        {initial?.evidence && <div className="card-editor-source"><EvidenceButton evidence={initial.evidence} onOpen={onEvidence} /></div>}
        <TextInput {...fieldAppearance} label={t("Суть карточки")} required maxLength={500} data-autofocus {...form.register('title')} error={localizeMessage(form.formState.errors.title?.message)} />
        <div className="card-editor-columns">
          <div className="card-editor-section">
            <Text className="card-editor-section-label" size="xs" fw={600} c="dimmed">{t("Содержание")}</Text>
            <Textarea {...fieldAppearance} label={t("Детали")} placeholder={t("Контекст и подробности поручения")} autosize minRows={4} maxRows={8} resize="none" maxLength={4000} {...form.register('description')} />
            <Controller name="quote" control={form.control} render={({ field }) => <Textarea {...fieldAppearance} label={t("Цитата из стенограммы")} placeholder={t("Вставьте точный фрагмент разговора")} description={t("Необязательно. Точный фрагмент стенограммы без изменений.")} inputWrapperOrder={['label', 'input', 'description', 'error']} autosize minRows={4} maxRows={8} resize="none" maxLength={2000} value={field.value ?? ''} onChange={(e) => { field.onChange(e.currentTarget.value || null); form.setValue('quote_start', null) }} />} />
            {!!initial?.revisions.length && <Disclosure label={t("Изменения в разговоре")}><CardRevisions card={initial} onOpen={onEvidence} /></Disclosure>}
          </div>
          <div className="card-editor-section">
            <Text className="card-editor-section-label" size="xs" fw={600} c="dimmed">{t("Параметры")}</Text>
            <div className="editor-grid">{select('kind', t("Тип карточки"), kinds)}{select('priority', t("Приоритет"), priorities)}</div>
            <Controller name="assignee" control={form.control} render={({ field }) => <TextInput {...fieldAppearance} label={t("Ответственный")} placeholder={t("Не указан")} maxLength={200} value={field.value ?? ''} onChange={(e) => field.onChange(e.currentTarget.value || null)} />} />
            <div className="editor-grid">
              <Controller name="due_date" control={form.control} render={({ field }) => <DatePickerInput {...fieldAppearance}
                label={t("Дата выполнения")} placeholder={t("Выберите дату")} locale={getLocale()} firstDayOfWeek={1}
                valueFormat={getLocale() === "en" ? "MMM D, YYYY" : "D MMM YYYY"} clearable clearButtonProps={{ 'aria-label': t("Очистить дату") }}
                leftSection={<CalendarDays size={18} aria-hidden="true" />} leftSectionPointerEvents="none"
                dropdownType={mobile ? 'modal' : 'popover'} popoverProps={{ withinPortal: false, position: 'bottom-start' }}
                modalProps={{ title: t("Выберите дату выполнения"), centered: true, size: 'auto', closeButtonProps: { 'aria-label': t("Закрыть календарь") } }}
                ariaLabels={{ monthLevelControl: t("Выбрать месяц"), yearLevelControl: t("Выбрать год"), nextMonth: t("Следующий месяц"), previousMonth: t("Предыдущий месяц"), nextYear: t("Следующий год"), previousYear: t("Предыдущий год"), nextDecade: t("Следующее десятилетие"), previousDecade: t("Предыдущее десятилетие") }}
                getDayAriaLabel={(date) => dayjs(date).locale(getLocale()).format('D MMMM YYYY')}
                value={field.value} onChange={field.onChange} onBlur={field.onBlur} ref={field.ref} name={field.name}
                error={localizeMessage(form.formState.errors.due_date?.message)} />} />
              <Controller name="due_text" control={form.control} render={({ field }) => <TextInput {...fieldAppearance} label={t("Срок как озвучен")} placeholder={t("Например: к пятнице")} maxLength={200} value={field.value ?? ''} onChange={(e) => field.onChange(e.currentTarget.value || null)} />} />
            </div>
            <div className="editor-grid">{select('status', t("Статус"), statuses)}{select('agreement', t("Договорённость"), agreements)}</div>
          </div>
        </div>
        <Controller name="reviewed" control={form.control} render={({ field }) => <Checkbox size="sm" label={t("Я проверил содержание, ответственного и срок")} checked={field.value} onChange={(e) => field.onChange(e.currentTarget.checked)} />} />
        {mutation.isError && <Alert color="red" role="alert">{boardError(mutation.error)}{conflict && <Button variant="subtle" onClick={() => { void onRefresh(); onClose() }}>{t("Закрыть и обновить доску")}</Button>}</Alert>}
      </div>
      <Flex className="card-editor-footer" justify="flex-end" gap="sm"><Button size="sm" variant="default" disabled={mutation.isPending} onClick={onClose}>{t("Отмена")}</Button><Button size="sm" type="submit" loading={mutation.isPending} disabled={conflict}>{t("Сохранить карточку")}</Button></Flex>
    </form>
  </Modal>
}
