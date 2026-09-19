import { t, useLocale } from '../../i18n'
import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Group, Text } from '@mantine/core'
import { Play } from 'lucide-react'
import { clipAudioUrl, type MeetingAudioClip } from '../../lib/meeting-audio'
import { timestamp } from '../../lib/transcription'

export function LiveClipPlayer({ meetingId, clips, autoplay = false }: {
  meetingId: string; clips: MeetingAudioClip[]; autoplay?: boolean
}) {
  useLocale()
  const [selected, setSelected] = useState(clips[0].id)
  const [requestedPlay, setRequestedPlay] = useState(autoplay)
  const clip = clips.find((item) => item.id === selected) ?? clips[0]
  return <div className="evidence-audio">
    <Text size="sm">{clip.speaker} · {timestamp(clip.start)}–{timestamp(clip.end)}</Text>
    {clips.length > 1 && <Group gap="xs">{clips.map((item) => <Button key={item.id} size="compact-sm" variant={item.id === clip.id ? 'light' : 'subtle'} onClick={() => { setSelected(item.id); setRequestedPlay(true) }}>{timestamp(item.start)} · {item.speaker}</Button>)}</Group>}
    <ClipAudio key={clip.id} src={clipAudioUrl(meetingId, clip.id)} autoplay={requestedPlay} />
  </div>
}

function ClipAudio({ src, autoplay }: { src: string; autoplay: boolean }) {
  useLocale()
  const player = useRef<HTMLAudioElement>(null)
  useEffect(() => { const audio = player.current; return () => audio?.pause() }, [])
  const [failed, setFailed] = useState(false)
  const [ready, setReady] = useState(false)
  async function play() {
    if (!player.current) return
    player.current.currentTime = 0
    try { await player.current.play() } catch { /* Native controls remain available if autoplay is blocked. */ }
  }
  return <>
    <audio ref={player} controls preload="metadata" aria-label={t("Аудио реплики")} src={src}
      onLoadedMetadata={() => { setReady(true); if (autoplay) void play() }} onError={() => setFailed(true)} />
    <Button variant="light" leftSection={<Play size={15} />} disabled={!ready || failed} onClick={() => void play()}>{t("Прослушать реплику")}</Button>
    {failed && <Alert color="orange">{t("Не удалось открыть аудио реплики. Закройте фрагмент и попробуйте ещё раз.")}</Alert>}
  </>
}
