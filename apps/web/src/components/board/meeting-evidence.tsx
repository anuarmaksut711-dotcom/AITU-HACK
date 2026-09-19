import { t, useLocale } from '../../i18n'
import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Modal, Text } from '@mantine/core'
import { FileText, Play } from 'lucide-react'
import type { Evidence } from '../../lib/board'
import type { MeetingDetail } from '../../lib/contracts'
import { timestamp } from '../../lib/transcription'
import { clipsForEvidence, useMeetingAudio } from '../../lib/meeting-audio'
import { LiveClipPlayer } from './live-clip-player'

export function EvidenceButton({ evidence, onOpen, label = t("Источник") }: {
  evidence: Evidence; onOpen: (value: Evidence) => void; label?: string
}) {
  useLocale()
  const time = evidence.start_seconds
  return <Button variant="subtle" size="compact-xs" leftSection={time !== null ? <Play size={13} /> : <FileText size={13} />}
    onClick={() => onOpen(evidence)}>{label}{time !== null ? ` · ${timestamp(time)}` : ''}</Button>
}

export function MeetingEvidence({ meeting, evidence, onClose, autoplay = false }: {
  meeting: MeetingDetail; evidence: Evidence; onClose: () => void; autoplay?: boolean
}) {
  useLocale()
  const audio = useRef<HTMLAudioElement>(null)
  const [audioError, setAudioError] = useState(false)
  const [ready, setReady] = useState(false)
  const [playError, setPlayError] = useState(false)
  const [playingExcerpt, setPlayingExcerpt] = useState(false)
  const recording = useMeetingAudio(meeting)
  const matchingClips = clipsForEvidence(recording.data?.clips ?? [], evidence)
  const availableClips = matchingClips.filter((clip) => clip.available)
  // API offsets count Unicode code points, including emoji, not UTF-16 units.
  const characters = Array.from(meeting.transcript)
  const matches = characters.slice(evidence.start_char, evidence.end_char).join('') === evidence.quote
  const hasAudio = matches && (meeting.source_type === 'audio' || recording.data?.full_audio_available) && evidence.start_seconds !== null
  useEffect(() => { const player = audio.current; return () => player?.pause() }, [hasAudio, availableClips.length])
  const before = characters.slice(Math.max(0, evidence.start_char - 240), evidence.start_char).join('')
  const after = characters.slice(evidence.end_char, evidence.end_char + 240).join('')
  async function play() {
    const player = audio.current
    if (!player || evidence.start_seconds === null) return
    player.currentTime = evidence.start_seconds
    setPlayError(false)
    setPlayingExcerpt(true)
    try { await player.play() } catch { setPlayingExcerpt(false); setPlayError(true) }
  }
  return <Modal opened onClose={onClose} title={t("Источник из встречи")} size="lg" centered
    closeButtonProps={{ 'aria-label': t("Закрыть источник") }}>
    <div className="meeting-evidence">
      <Text fw={600}>{meeting.title}</Text>
      {!matches ? <Alert color="orange">{t("Этот фрагмент больше не совпадает со стенограммой. Закройте источник и обновите страницу встречи.")}</Alert> : <>
        {evidence.speaker && <Text size="sm">{evidence.speaker}</Text>}
        <div className="evidence-context" data-testid="evidence-context">
          {evidence.start_char > 240 && '…'}{before}<mark>{evidence.quote}</mark>{after}
          {evidence.end_char + 240 < characters.length && '…'}
        </div>
        {availableClips.length > 0 ? <LiveClipPlayer meetingId={meeting.id} clips={availableClips} autoplay={autoplay} /> : hasAudio ? <div className="evidence-audio">
          <Text size="sm">{t("Фрагмент записи:")} {timestamp(evidence.start_seconds!)}{evidence.end_seconds !== null ? `–${timestamp(evidence.end_seconds)}` : ''}</Text>
          <audio ref={audio} controls preload="metadata" aria-label={t("Исходная аудиозапись")}
            src={`/api/v1/meetings/${encodeURIComponent(meeting.id)}/audio`}
            onLoadedMetadata={() => { setReady(true); if (audio.current) audio.current.currentTime = evidence.start_seconds!; if (autoplay) void play() }}
            onTimeUpdate={() => { if (playingExcerpt && audio.current && evidence.end_seconds !== null && audio.current.currentTime >= evidence.end_seconds) { audio.current.pause(); setPlayingExcerpt(false) } }}
            onEnded={() => setPlayingExcerpt(false)} onError={() => setAudioError(true)} />
          <Button variant="light" leftSection={<Play size={15} />} disabled={!ready || audioError} onClick={() => void play()}>{t("Прослушать фрагмент")}</Button>
          {audioError && <Alert color="orange">{t("Не удалось открыть аудиозапись. Цитату можно проверить по стенограмме выше.")}</Alert>}
          {playError && <Alert color="orange">{t("Не удалось начать воспроизведение. Попробуйте кнопку воспроизведения на аудиоплеере.")}</Alert>}
        </div> : recording.isFetching ? <Text size="sm" role="status">{t("Проверяем аудио фрагмента…")}</Text> : recording.isError ? <Alert color="orange">{t("Не удалось проверить аудио.")} <Button variant="subtle" onClick={() => void recording.refetch()}>{t("Повторить")}</Button></Alert> : <Text size="sm" c="dimmed">{t("Аудио этого фрагмента не сохранено. Доступна только стенограмма.")}</Text>}
        {availableClips.length > 0 && availableClips.length < matchingClips.length && <Text size="sm" c="dimmed">{t("Часть аудиореплик этого фрагмента не сохранилась.")}</Text>}
      </>}
    </div>
  </Modal>
}
