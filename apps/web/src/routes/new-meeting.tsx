import { t, useLocale } from '../i18n'
import { Link, useNavigate } from '@tanstack/react-router'
import { Anchor } from '@mantine/core'
import { ArrowLeft } from 'lucide-react'
import type { MeetingDetail } from '../lib/contracts'
import { meetingQuery, queryClient } from '../lib/query'
import { PageHeading } from '../components/ui'
import { AudioUpload } from '../components/audio-upload'

export function NewMeetingPage() {
  useLocale()
  const navigate = useNavigate()
  async function onSuccess(meeting: MeetingDetail) {
    queryClient.setQueryData(meetingQuery(meeting.id).queryKey, meeting)
    void queryClient.invalidateQueries({ queryKey: ['meetings', 'list'] })
    await navigate({ to: '/meetings/$meetingId', params: { meetingId: meeting.id } })
  }
  return <div className="page meetings-library">
    <Anchor className="page-back" renderRoot={(props) => <Link {...props} to="/meetings" search={{ q: '', offset: 0 }} />}><ArrowLeft size={19} aria-hidden="true" />{t("К встречам")}</Anchor>
    <header className="page-header"><PageHeading title={t("Загрузить аудио")} /></header>
    <section className="form-panel"><AudioUpload onSuccess={onSuccess} /></section>
  </div>
}
