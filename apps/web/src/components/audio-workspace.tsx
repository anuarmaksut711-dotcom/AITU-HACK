import { t, useLocale } from '../i18n'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Anchor, Button, Group, Progress, Stack, Tabs, Text, Title } from '@mantine/core'
import { ArrowRight, AudioLines, Check, CheckSquare, CircleHelp, Lightbulb, ListChecks, Sparkles, TriangleAlert } from 'lucide-react'
import type { MeetingDetail } from '../lib/contracts'
import { boardApi, boardError, type Evidence } from '../lib/board'
import { resolveMeetingEvidence, transcriptSegmentRanges } from '../lib/meeting-evidence'
import { clipsForEvidence, retryMeetingRecording, useMeetingAudio } from '../lib/meeting-audio'
import { timestamp } from '../lib/transcription'
import { Transcription } from './transcription'
import { MeetingBoard } from './board/meeting-board'
import { MeetingEvidence } from './board/meeting-evidence'
import { InlineError } from './ui'
import './live/live.css'
import './audio-workspace.css'
import './meeting-workspace.css'

export type MeetingView = 'conversation' | 'insights' | 'kanban'
const noteKinds = {
  task: { get label() { return t("Поручение") }, icon: CheckSquare },
  decision: { get label() { return t("Решение") }, icon: Check },
  topic: { get label() { return t("Тема") }, icon: Lightbulb },
  question: { get label() { return t("Открытый вопрос") }, icon: CircleHelp },
  risk: { get label() { return t("Риск") }, icon: TriangleAlert },
}

export function MeetingWorkspace({ meeting, view, onViewChange }: {
  meeting: MeetingDetail; view: MeetingView; onViewChange: (view: MeetingView) => void
}) {
  useLocale()
  const cache = useQueryClient()
  const pane = useRef<HTMLDivElement>(null)
  const audio = useRef<HTMLAudioElement>(null)
  useEffect(() => { const player = audio.current; return () => player?.pause() }, [view])
  const pendingSeek = useRef<number | null>(null)
  const [focusedSource, setFocusedSource] = useState<Evidence | null>(null)
  const [openedSource, setOpenedSource] = useState<Evidence | null>(null)
  const [playOnOpen, setPlayOnOpen] = useState(false)
  const following = useRef(true)
  const savedScroll = useRef<Record<MeetingView, number>>({ conversation: 0, insights: 0, kanban: 0 })
  const quoteTarget = useRef<{ quote: string; evidence: Evidence | null } | null>(null)
  const isAudio = meeting.source_type === 'audio'
  const recording = useMeetingAudio(meeting)
  const canPlayFull = isAudio || recording.data?.full_audio_available === true
  const retryRecording = useMutation({ mutationFn: () => retryMeetingRecording(meeting.id),
    onSuccess: () => cache.invalidateQueries({ queryKey: ['meeting-audio', meeting.id] }) })
  const processing = ['queued', 'running'].includes(meeting.transcription?.status ?? '')
  const recognized = !!meeting.transcript.trim() && (!isAudio || meeting.status === 'transcribed')
  const board = useQuery({ queryKey: ['board', meeting.id], queryFn: ({ signal }) => boardApi.get(meeting.id, signal),
    refetchInterval: (query) => processing || ['queued', 'running'].includes(query.state.data?.status ?? '') ? 1500 : false })
  useEffect(() => { if (recognized) void cache.invalidateQueries({ queryKey: ['board', meeting.id] }) }, [recognized, meeting.id, cache])
  const generation = useMutation({ mutationFn: () => boardApi.generate(meeting.id), onSuccess: (data) => cache.setQueryData(['board', meeting.id], data) })
  const analyzing = ['queued', 'running'].includes(board.data?.status ?? '')
  const complete = recognized && board.data?.status === 'ready'
  const segments = meeting.segments ?? []
  const ranges = useMemo(() => transcriptSegmentRanges(meeting), [meeting])
  const processedSeconds = segments.at(-1)?.end ?? 0
  const duration = meeting.transcription?.duration_seconds
  const notes = board.data?.cards.filter((card) => card.status !== 'dismissed') ?? []

  function changeView(next: MeetingView) {
    if (pane.current) savedScroll.current[view] = pane.current.scrollTop
    onViewChange(next)
  }
  function source(quote: string, evidence: Evidence | null) {
    quoteTarget.current = { quote, evidence }
    setFocusedSource(evidence)
    changeView('conversation')
  }
  function openSource(evidence: Evidence, play = false) { audio.current?.pause(); setPlayOnOpen(play); setOpenedSource(evidence) }
  useEffect(() => {
    const element = pane.current
    if (!element) return
    element.scrollTop = savedScroll.current[view]
    const target = quoteTarget.current
    if (view !== 'conversation' || !target) return
    quoteTarget.current = null
    const rows = Array.from(element.querySelectorAll<HTMLElement>('[data-transcript-segment]'))
    const reference = target.evidence
    const matches = rows.filter((node) => reference && node.dataset.startChar !== undefined
      ? Number(node.dataset.startChar) < reference.end_char && Number(node.dataset.endChar) > reference.start_char
      : reference?.start_seconds !== null && reference?.start_seconds !== undefined
        ? Number(node.dataset.start) <= reference.start_seconds && Number(node.dataset.end) > reference.start_seconds
        : node.textContent?.includes(target.quote))
    const row = reference ? matches[0] : matches.length === 1 ? matches[0] : undefined
    if (row) {
      row.scrollIntoView({ block: 'center', behavior: 'instant' })
      row.focus({ preventScroll: true })
    } else {
      const raw = element.querySelector<HTMLElement>('[data-testid="transcript"]')
      raw?.scrollIntoView({ block: 'start', behavior: 'instant' })
      raw?.focus({ preventScroll: true })
    }
    if (reference?.start_seconds !== null && reference?.start_seconds !== undefined && audio.current) {
      if (audio.current.readyState >= 1) audio.current.currentTime = reference.start_seconds
      else { pendingSeek.current = reference.start_seconds; audio.current.load() }
    }
  }, [view, focusedSource])
  useEffect(() => {
    if (processing && following.current && pane.current && view === 'conversation') pane.current.scrollTop = pane.current.scrollHeight
  }, [processing, segments.length, view])

  return <section className="meeting-workspace" aria-label={t("Рабочее пространство встречи")}>
    {isAudio && (processing || analyzing || (!recognized && meeting.status !== 'transcribed')) && <div className="meeting-processing no-print">
      <div className="audio-processing-header">
        <div className={`audio-processing-icon ${processing || analyzing ? 'is-processing' : ''}`} aria-hidden="true">{complete ? <Check size={23} /> : <AudioLines size={23} />}</div>
        <div className="audio-processing-copy">
          <Text fw={500} role="status">{processing ? meeting.transcription?.status === 'queued' ? t("Запись в очереди") : t("Распознаём запись") : analyzing ? t("Готовим итоги") : t("Обработка остановлена")}</Text>
          <Text size="sm" c="dimmed">{processing ? t("{0}{1} · Текст появляется по мере распознавания", { "0": timestamp(processedSeconds), "1": duration ? t(" из {0}", { "0": timestamp(duration) }) : '' }) : analyzing ? t("Итоги дополняются по мере анализа записи") : meeting.audio_filename}</Text>
        </div>
      </div>
      {(processing || analyzing) && <Progress value={processing ? meeting.transcription?.progress ?? 0 : board.data?.progress ?? 0} aria-label={processing ? t("Распознавание записи") : t("Подготовка итогов")} size={3} />}
      <Transcription meeting={meeting} compact />
    </div>}
    <Tabs value={view} onChange={(value) => { if (value) changeView(value as MeetingView) }} keepMounted={false} className="live-mode-tabs saved-meeting-tabs">
      <Tabs.List justify="center" aria-label={t("Разделы встречи")}>
        <Tabs.Tab value="conversation">{t("Разговор")}</Tabs.Tab>
        <Tabs.Tab value="insights">{t("Итоги")}</Tabs.Tab>
        <Tabs.Tab value="kanban">{t("Канбан")}</Tabs.Tab>
      </Tabs.List>
      <div className={`live-focus-pane saved-meeting-pane ${view === 'kanban' ? 'is-kanban' : ''}`} ref={pane}
        onScroll={(event) => { const element = event.currentTarget; savedScroll.current[view] = element.scrollTop; if (view === 'conversation') following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80 }}>
        <Tabs.Panel value="conversation" className="saved-conversation">
          {canPlayFull && <div className="audio-source"><Text size="sm" fw={500} mb="xs">{t("Запись всей беседы")}</Text><audio ref={audio} controls preload="none" aria-label={t("Аудиозапись встречи")} src={`/api/v1/meetings/${encodeURIComponent(meeting.id)}/audio`}
            onLoadedMetadata={() => { if (audio.current && pendingSeek.current !== null) { audio.current.currentTime = pendingSeek.current; pendingSeek.current = null } }} /></div>}
          {['queued', 'processing'].includes(recording.data?.recording_status ?? '') && <Text size="sm" role="status" mb="md">{t("Сохраняем полную аудиозапись беседы…")}</Text>}
          {recording.data?.source === 'live' && !canPlayFull && recording.data.recording_status === 'none' && <Text size="sm" c="dimmed" mb="md">{t("Полная аудиозапись этой беседы не сохранилась.")}</Text>}
          {recording.data?.recording_status === 'failed' && <InlineError>{t("Не удалось собрать полную запись.")} <Button variant="subtle" loading={retryRecording.isPending} onClick={() => retryRecording.mutate()}>{t("Повторить сохранение")}</Button></InlineError>}
          {retryRecording.isError && <InlineError>{t("Не удалось повторить сохранение. Обновите страницу и попробуйте ещё раз.")}</InlineError>}
          <section aria-label={t("Стенограмма разговора")} data-testid="meeting-conversation">
            {segments.length ? segments.map((segment, index) => <article data-transcript-segment data-start={segment.start} data-end={segment.end} data-start-char={ranges[index]?.start} data-end-char={ranges[index]?.end} tabIndex={-1} key={`${index}-${segment.start}`} className="live-utterance saved-utterance">
              <Text size="sm" c="dimmed" component="time">{timestamp(segment.start)}</Text><Text className="live-utterance-text"><SourceHighlight text={segment.text} offset={ranges[index]?.start} selection={focusedSource} /></Text>
              {ranges[index] && (canPlayFull || (recording.data?.clips ?? []).some((clip) => clip.available && clip.start_char < ranges[index].end && clip.end_char > ranges[index].start)) && <Button variant="subtle" size="compact-sm" mt="xs" onClick={() => openSource({ quote: segment.text, start_char: ranges[index].start, end_char: ranges[index].end, start_seconds: segment.start, end_seconds: segment.end, speaker: null }, true)}>{t("Прослушать реплику")}</Button>}
            </article>) : meeting.transcript ? <div className="live-utterance-text saved-transcript" data-testid="transcript" tabIndex={-1}><SourceHighlight text={meeting.transcript} offset={0} selection={focusedSource} /></div> : <div className="live-empty"><AudioLines size={28} aria-hidden="true" /><Title order={2}>{t("Здесь появится разговор")}</Title><Text c="dimmed">{processing ? t("Первые реплики появятся по мере распознавания записи.") : t("Распознайте запись, чтобы прочитать стенограмму.")}</Text></div>}
            {processing && segments.length > 0 && <Text className="audio-feed-continuation" size="sm" c="dimmed">{t("Продолжаем распознавать…")}</Text>}
          </section>
        </Tabs.Panel>
        <Tabs.Panel value="insights" className="saved-insights">
          {board.isPending && <Text role="status">{t("Загружаем итоги…")}</Text>}
          {board.isError && <InlineError>{boardError(board.error)} <Button variant="subtle" onClick={() => void board.refetch()}>{t("Повторить загрузку итогов")}</Button></InlineError>}
          {!board.isError && board.data && <>
            {analyzing && <Text size="sm" c="dimmed" mb="md">{t("Готовим итоги встречи. Промежуточные выводы могут уточняться.")}</Text>}
            {board.data.status === 'failed' && <InlineError>{boardError(board.data.error_code)} {t("Промежуточные выводы могут быть неполными.")}</InlineError>}
            <Stack gap="md">{notes.map((card) => {
              const kind = noteKinds[card.kind]; const Icon = kind.icon
              const evidence = card.quote ? resolveMeetingEvidence(meeting, card.quote, card.evidence, card.start_char) : null
              const matching = clipsForEvidence(recording.data?.clips ?? [], evidence)
              const playable = evidence !== null && ((canPlayFull && evidence.start_seconds !== null) || matching.some((clip) => clip.available))
              return <article className="live-note saved-insight-note" key={card.id}><div className="live-note-icon"><Icon size={24} aria-hidden="true" /></div><div className="live-note-copy"><Text className="live-note-kind">{kind.label}</Text><Text className="live-note-text">{card.title}</Text>{card.description && <Text size="sm" c="dimmed" mt="xs">{card.description}</Text>}
                {card.quote && <SavedSource quote={card.quote} evidence={evidence} onConversation={source} onOpen={openSource}
                  onListen={playable && evidence ? () => openSource(evidence, true) : undefined}
                  audioMissing={!playable && matching.length > 0 && ['none', 'ready', 'failed'].includes(recording.data?.recording_status ?? '')} />}</div>
              </article>
            })}</Stack>
            {!notes.length && <div className="live-empty"><Sparkles size={29} aria-hidden="true" /><Title order={2}>{processing ? t("Сначала распознаем разговор") : analyzing ? t("Готовим первые итоги") : t("Итогов пока нет")}</Title><Text c="dimmed">{t("Здесь появятся темы, решения, поручения и открытые вопросы встречи.")}</Text></div>}
            {recognized && board.data.status === 'failed' && <Group justify="center" mt="lg"><Button loading={generation.isPending} onClick={() => generation.mutate()}>{t("Повторить анализ")}</Button></Group>}
            {generation.isError && <InlineError>{boardError(generation.error)}</InlineError>}
            {notes.some((card) => card.kind === 'task') && <Group justify="center" mt="lg"><Button variant="light" leftSection={<ListChecks size={18} />} onClick={() => changeView('kanban')}>{t("К поручениям в канбане")}</Button></Group>}
          </>}
        </Tabs.Panel>
        <Tabs.Panel value="kanban" className="saved-kanban">
          <MeetingBoard key={`kanban-${meeting.id}`} embedded meetingId={meeting.id} title={meeting.title} meeting={meeting} canGenerate={recognized} />
        </Tabs.Panel>
      </div>
    </Tabs>
    {openedSource && <MeetingEvidence key={`${openedSource.start_char}:${openedSource.end_char}`} meeting={meeting} evidence={openedSource} autoplay={playOnOpen} onClose={() => setOpenedSource(null)} />}
  </section>
}

function SourceHighlight({ text, offset, selection }: { text: string; offset?: number; selection: Evidence | null }) {
  useLocale()
  if (offset === undefined || !selection) return text
  const characters = Array.from(text)
  const start = Math.max(0, selection.start_char - offset)
  const end = Math.min(characters.length, selection.end_char - offset)
  if (start >= end) return text
  return <>{characters.slice(0, start).join('')}<mark className="meeting-source-highlight">{characters.slice(start, end).join('')}</mark>{characters.slice(end).join('')}</>
}

function SavedSource({ quote, evidence, onConversation, onOpen, onListen, audioMissing }: {
  quote: string; evidence: Evidence | null; onConversation: (quote: string, evidence: Evidence | null) => void; onOpen: (evidence: Evidence) => void; onListen?: () => void; audioMissing?: boolean
}) {
  useLocale()
  return <div className="saved-insight-source">
    <Text size="xs" c="dimmed">{t("Из разговора")}{evidence?.speaker ? ` · ${evidence.speaker}` : ''}{evidence?.start_seconds !== null && evidence?.start_seconds !== undefined ? ` · ${timestamp(evidence.start_seconds)}` : ''}</Text>
    <blockquote>{quote}</blockquote>
    <Group gap="md" wrap="wrap">
      {onListen && <Button variant="light" size="compact-sm" onClick={onListen}>{t("Прослушать момент")}</Button>}
      {evidence && <Button variant="subtle" size="compact-sm" onClick={() => onOpen(evidence)}>{t("Открыть фрагмент")}</Button>}
      <Anchor component="button" className="live-source-link" onClick={() => onConversation(quote, evidence)}>{t("К разговору")}<ArrowRight size={15} aria-hidden="true" /></Anchor>
    </Group>
    {audioMissing && <Text size="xs" c="dimmed">{t("Аудио этого момента не сохранилось.")}</Text>}
  </div>
}
