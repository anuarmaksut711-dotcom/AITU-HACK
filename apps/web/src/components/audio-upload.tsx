import { t, useLocale, localizeMessage } from '../i18n'
import { useState, type FormEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Button, FileInput, Select, TextInput } from '@mantine/core'
import { Upload } from 'lucide-react'
import { api, errorMessage } from '../lib/api'
import type { MeetingDetail } from '../lib/contracts'
import { InlineError } from './ui'
import './transcription.css'

export function AudioUpload({ onSuccess, initialFile = null }: { initialFile?: File | null; onSuccess: (meeting: MeetingDetail) => Promise<void> }) {
  useLocale()
  const [title, setTitle] = useState(initialFile?.name.replace(/\.[^.]+$/, '').slice(0, 200) ?? '')
  const [language, setLanguage] = useState('auto')
  const [file, setFile] = useState<File | null>(initialFile)
  const [validation, setValidation] = useState('')
  const upload = useMutation({ mutationFn: api.uploadAudio, onSuccess })
  function submit(event: FormEvent) {
    event.preventDefault()
    if (!title.trim()) { setValidation(t("Введите название встречи.")); return }
    if (!file) { setValidation(t("Выберите аудиофайл.")); return }
    if (!/\.(mp3|wav|m4a)$/i.test(file.name)) { setValidation(t("Поддерживаются MP3, WAV и M4A.")); return }
    if (!file.size || file.size > 100 * 1024 * 1024) { setValidation(t("Выберите непустой файл размером до 100 МБ.")); return }
    setValidation('')
    upload.mutate({ title: title.trim(), language, file })
  }
  return <form className="meeting-form" aria-label={t("Аудиозапись")} noValidate onSubmit={submit} aria-busy={upload.isPending}>
    <TextInput label={t("Название встречи")} id="audio-title" maxLength={200} value={title} onChange={(event) => setTitle(event.target.value)} disabled={upload.isPending} />
    <Select label={t("Язык записи")} id="audio-language" value={language} onChange={(value) => { if (value) setLanguage(value) }} disabled={upload.isPending}
      data={[{ value: 'auto', label: t("Определить автоматически") }, { value: 'ru', label: t("Русский") }, { value: 'kk', label: t("Қазақша") }, { value: 'en', label: t("English") }]} />
    <FileInput label={t("Аудиозапись встречи")} id="audio-file" placeholder={t("Выбрать файл")} description={t("MP3, WAV или M4A · до 100 МБ · до 2 часов")}
      accept=".mp3,.wav,.m4a" leftSection={<Upload size={18} aria-hidden="true" />} value={file} onChange={(value) => { setFile(value); if (!title.trim() && value) setTitle(value.name.replace(/\.[^.]+$/, '').slice(0, 200)); setValidation('') }}
      disabled={upload.isPending} clearable clearButtonProps={{ 'aria-label': t("Убрать выбранный файл") }} />
    {validation && <InlineError>{localizeMessage(validation)}</InlineError>}
    {upload.isError && <InlineError>{errorMessage(upload.error, t("Не удалось загрузить аудио. Файл остался выбранным — попробуйте ещё раз."))}</InlineError>}
    <div className="form-actions"><Button type="submit" loading={upload.isPending}>{upload.isPending ? t("Загружаем аудио…") : t("Распознать запись")}</Button></div>
  </form>
}
