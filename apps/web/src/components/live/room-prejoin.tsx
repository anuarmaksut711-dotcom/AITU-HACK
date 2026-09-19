import { t, useLocale } from '../../i18n'
import { useState, type FormEvent } from 'react'
import { Button, Stack, Text, TextInput, Title } from '@mantine/core'
import { Mic } from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import { liveApi, liveError, saveGrant, type LiveGrant, type LiveRoom } from '../../lib/live'
import { InlineError } from '../ui'

export function RoomPrejoin({ room, grant, invite, onJoin }: {
  room: LiveRoom; grant: LiveGrant | null; invite: string | null; onJoin: (grant: LiveGrant) => void;
}) {
  useLocale()
  const [name, setName] = useState(grant?.name ?? '')
  const join = useMutation({ mutationFn: async () => {
    if (grant) return room.status === 'active' ? liveApi.refresh(grant) : grant
    if (!invite || !name.trim()) throw new Error('NAME_REQUIRED')
    return liveApi.join(room.id, invite, name.trim())
  }, onSuccess: (value) => { saveGrant(value); onJoin(value) } })
  function submit(event: FormEvent) { event.preventDefault(); join.mutate() }
  return <section className="live-prejoin">
    <div className="live-start-icon"><Mic size={28} /></div>
    <Title order={1}>{room.title}</Title>
    <Text c="dimmed">{room.status === 'active' ? t("Голосовая встреча · без камеры") : t("Встреча завершена")}</Text>
    {room.status !== 'active' && !grant ? <Text mt="lg" c="dimmed">{t("Войти в завершённую встречу уже нельзя. За итогами обратитесь к организатору.")}</Text> : <form onSubmit={submit}><Stack gap="lg">
      {!grant && <TextInput label={t("Как вас представить?")} value={name} onChange={(event) => setName(event.currentTarget.value)} maxLength={80} required autoComplete="name" />}
      <Text size="sm" c="dimmed">{t("Разговор записывается. Аудиозапись и стенограмма сохранятся у организатора. Речь преобразуется в текст локально; текст доступен участникам комнаты и передаётся в OpenAI для анализа.")}</Text>
      {join.isError && <InlineError>{liveError(join.error)}</InlineError>}
      <Button type="submit" leftSection={<Mic size={18} />} disabled={!grant && !name.trim()} loading={join.isPending}>{room.status === 'active' ? t("Войти в разговор") : t("Открыть итоги")}</Button>
    </Stack></form>}
  </section>
}
