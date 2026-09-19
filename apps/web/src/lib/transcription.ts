import { t } from '../i18n'
import type { MeetingSummary } from './contracts'

export function meetingStatus(meeting: MeetingSummary): string {
  const status = meeting.transcription?.status
  if (status === 'queued') return t("Ожидает распознавания")
  if (status === 'running') return t("Распознаём запись")
  if (status === 'failed') return t("Не удалось распознать")
  if (status === 'cancelled') return t("Распознавание отменено")
  return meeting.status === 'transcribed' ? t("Стенограмма готова") : t("Черновик")
}
export function timestamp(seconds: number) {
  const value = Math.floor(seconds)
  return `${Math.floor(value / 60).toString().padStart(2, '0')}:${(value % 60).toString().padStart(2, '0')}`
}
