import { t, useLocale } from '../i18n'
import { useMutation } from '@tanstack/react-query'
import { Button, Progress, Text } from '@mantine/core'
import { api, errorMessage } from '../lib/api'
import type { MeetingDetail } from '../lib/contracts'
import { meetingQuery, queryClient } from '../lib/query'
import { InlineError } from './ui'
import './transcription.css'

const errors: Record<string, string> = {
  get model_missing() { return t("Распознавание ещё не настроено. Установите локальную модель по инструкции запуска и повторите попытку.") },
  get invalid_audio() { return t("Не удалось прочитать аудио. Проверьте, что запись воспроизводится, и загрузите её в формате MP3, WAV или M4A.") },
  get audio_too_long() { return t("Запись длиннее 2 часов. Разделите её на части и загрузите снова.") },
  get no_speech() { return t("В записи не удалось обнаружить речь. Проверьте громкость и выбранный язык.") },
  get source_changed() { return t("Исходная запись изменилась. Загрузите файл заново.") },
  get model_changed() { return t("Модель распознавания изменилась. Повторите попытку.") },
  get transcript_too_long() { return t("Стенограмма превышает допустимый объём. Разделите запись на части.") },
  get processing_timeout() { return t("Распознавание заняло слишком много времени. Попробуйте запись меньшей длительности.") },
  get worker_interrupted() { return t("Распознавание прервалось. Запись сохранена — можно повторить попытку.") },
}
export function Transcription({ meeting, compact = false }: { meeting: MeetingDetail; compact?: boolean }) {
  useLocale()
  const action = useMutation({
    mutationFn: () => api.retryTranscription(meeting.id),
    onSuccess: async (result) => {
      queryClient.setQueryData(meetingQuery(meeting.id).queryKey, result)
      await queryClient.invalidateQueries({ queryKey: ['meetings', 'list'] })
    },
  })
  const job = meeting.transcription
  if (!job) return null
  const active = job.status === 'queued' || job.status === 'running'
  if (compact && (job.status === 'succeeded' || active)) return null
  return <section className="transcription-state" aria-label={t("Распознавание аудио")}>
    {!compact && <Text size="sm" className="audio-filename">{meeting.audio_filename}</Text>}
    {active && <>
      {!compact && <Text size="sm" role="status">{job.status === 'queued' ? t("Запись ожидает распознавания. Можно вернуться к ней позже.") : t("Распознаём запись · {0}%", { "0": job.progress })}</Text>}
      {!compact && job.status === 'running' && <Progress size="md" value={job.progress} aria-label={t("Прогресс распознавания")} className="transcription-progress" />}
    </>}
    {job.status === 'failed' && <InlineError>{errors[job.error_code ?? ''] ?? t("Не удалось распознать запись. Попробуйте ещё раз.")}</InlineError>}
    {job.status === 'cancelled' && <Text size="sm">{t("Распознавание отменено. Запись сохранена.")}</Text>}
    {(job.status === 'failed' || job.status === 'cancelled') && <Button loading={action.isPending} onClick={() => action.mutate()}>{t("Повторить распознавание")}</Button>}
    {action.isError && <InlineError>{errorMessage(action.error)}</InlineError>}
  </section>
}
