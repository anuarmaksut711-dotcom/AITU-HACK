import { t, useLocale, formatNumber } from '../i18n'
import { useState, type FormEvent } from 'react'
import { getRouteApi, Link, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, FileText, Upload, Search, X } from 'lucide-react'
import { ActionIcon, Badge, Button, FileButton, Modal, Paper, Text, TextInput } from '@mantine/core'
import { errorMessage } from '../lib/api'
import { meetingQuery, queryClient, meetingsQuery } from '../lib/query'
import { EmptyState, ErrorState, LoadingState, PageHeading } from '../components/ui'
import { AudioUpload } from '../components/audio-upload'
import type { MeetingDetail } from '../lib/contracts'
import { meetingStatus } from '../lib/transcription'

const route = getRouteApi('/_workspace/meetings')

function SearchForm({ value, onSearch }: { value: string; onSearch: (value: string) => void }) {
  useLocale()
  const [text, setText] = useState(value)
  function submit(event: FormEvent) { event.preventDefault(); onSearch(text.trim()) }
  return <form className="search-form" role="search" onSubmit={submit}>
    <div className="search-input-wrap">
      <TextInput label={t("Поиск по названию")} className="search-input" id="meeting-search" type="search" placeholder={t("Введите название встречи")} value={text} maxLength={200} onChange={(event) => setText(event.target.value)}
        leftSection={<Search size={20} aria-hidden="true" />}
        rightSection={text && <ActionIcon size="lg" variant="subtle" color="gray" type="button" aria-label={t("Сбросить поиск")} onClick={() => { setText(''); onSearch('') }}><X size={18} aria-hidden="true" /></ActionIcon>} />
      <Button variant="default" type="submit">{t("Найти")}</Button>
    </div>
  </form>
}

export function MeetingsPage() {
  useLocale()
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const { q, offset } = route.useSearch()
  const navigate = useNavigate()
  const meetings = useQuery(meetingsQuery(q, offset))
  const changeSearch = (value: string) => { void navigate({ to: '/meetings', search: { q: value, offset: 0 } }) }
  const changePage = (value: number) => { void navigate({ to: '/meetings', search: { q, offset: value } }) }
  async function uploaded(meeting: MeetingDetail) {
    queryClient.setQueryData(meetingQuery(meeting.id).queryKey, meeting)
    void queryClient.invalidateQueries({ queryKey: ['meetings', 'list'] })
    setUploadOpen(false)
    await navigate({ to: '/meetings/$meetingId', params: { meetingId: meeting.id } })
  }
  const uploadButton = <FileButton accept=".mp3,.wav,.m4a" onChange={(file) => { if (file) { setUploadFile(file); setUploadOpen(true) } }}>
    {(props) => <Button {...props} leftSection={<Upload size={18} aria-hidden="true" />}>{t("Загрузить аудио")}</Button>}
  </FileButton>
  return <div className="page meetings-library">
    <Modal opened={uploadOpen} onClose={() => setUploadOpen(false)} title={t("Загрузить аудио")} centered keepMounted={false}>
      <AudioUpload initialFile={uploadFile} onSuccess={uploaded} />
    </Modal>
    <header className="page-header">
      <PageHeading title={t("Встречи")} />
      {uploadButton}
    </header>
    <div className="library-toolbar"><SearchForm key={q} value={q} onSearch={changeSearch} /></div>
    {meetings.isPending ? <LoadingState /> : meetings.isError ? <ErrorState description={errorMessage(meetings.error)} onRetry={() => void meetings.refetch()} /> : meetings.data.items.length === 0 ? (
      q ? <EmptyState title={t("Ничего не найдено")} description={t("Попробуйте другое название или сбросьте поиск.")}>
        <Button variant="default" onClick={() => changeSearch('')}>{t("Сбросить поиск")}</Button>
      </EmptyState> : offset > 0 ? <EmptyState title={t("На этой странице нет встреч")} description={t("Вернитесь к началу списка.")}>
        <Button variant="default" onClick={() => changePage(0)}>{t("К первой странице")}</Button>
      </EmptyState> : <EmptyState title={t("Здесь будут ваши встречи")} description={t("Загрузите аудиозапись. Стенограмма будет появляться по ходу распознавания, затем начнётся подготовка итогов.")}>
        {uploadButton}
      </EmptyState>
    ) : <>
      <div className="meeting-list" aria-label={t("Список встреч")}>
        {meetings.data.items.map((meeting) => <Paper withBorder p="sm" key={meeting.id} className="meeting-row"
          renderRoot={(props) => <Link {...props} data-testid="meeting-row" aria-label={meeting.title} to="/meetings/$meetingId" params={{ meetingId: meeting.id }} />}>
          <span className="meeting-file-icon"><FileText size={25} strokeWidth={1.6} aria-hidden="true" /></span>
          <span className="meeting-main"><Text component="span" size="md" fw={500} className="meeting-title">{meeting.title}</Text><span className="meeting-meta"><Badge color="gray" variant="light" radius="xl" tt="none">{meetingStatus(meeting)}</Badge></span></span>
          <ChevronRight className="meeting-arrow" size={21} aria-hidden="true" />
        </Paper>)}
      </div>
      {(offset > 0 || meetings.data.total > 20) && <nav className="pagination" aria-label={t("Страницы встреч")}>
        <Button variant="default" disabled={offset === 0} onClick={() => changePage(Math.max(0, offset - 20))} leftSection={<ChevronLeft size={18} aria-hidden="true" />}>{t("Назад")}</Button>
        <Text size="xs" c="dimmed" aria-live="polite">{t("{start}–{end} из {total}", { start: formatNumber(offset + 1), end: formatNumber(offset + meetings.data.items.length), total: formatNumber(meetings.data.total) })}</Text>
        <Button variant="default" disabled={offset + 20 >= meetings.data.total || offset + 20 > 100_000} onClick={() => changePage(offset + 20)} rightSection={<ChevronRight size={18} aria-hidden="true" />}>{t("Далее")}</Button>
      </nav>}
    </>}
  </div>
}
