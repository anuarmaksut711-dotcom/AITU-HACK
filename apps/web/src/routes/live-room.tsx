import { t, useLocale } from '../i18n'
import { useEffect, useState, type ReactNode } from 'react'
import { getRouteApi, Link, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { Anchor, Button } from '@mantine/core'
import { api, ApiError } from '../lib/api'
import { consumeAutoJoin, RoomAccessError, liveApi, liveError, saveGrant, storedGrant, type LiveGrant } from '../lib/live'
import { LanguageSwitcher } from '../components/language-switcher'
import { Brand, ErrorState } from '../components/ui'
import { WorkspaceFrame } from '../components/shell'
import { LiveConnecting } from '../components/live/live-connecting'
import { RoomPrejoin } from '../components/live/room-prejoin'
import { RoomWorkspace } from '../components/live/room-workspace'
import '../components/live/live.css'

const route = getRouteApi('/live/$roomId')

export function LiveRoomPage() {
  useLocale()
  const { roomId } = route.useParams()
  const { view, focus } = route.useSearch()
  const navigate = useNavigate()
  const [invite] = useState(() => new URLSearchParams(location.hash.slice(1)).get('invite'))
  const [grant, setGrant] = useState<LiveGrant | null>(() => storedGrant(roomId))
  const [joined, setJoined] = useState(() => sessionStorage.getItem(`soyle-live-autojoin:${roomId}`) === '1')
  useEffect(() => { consumeAutoJoin(roomId) }, [roomId])
  const entry = useQuery({ queryKey: ['live', 'entry', roomId, !!invite], retry: false, queryFn: async () => {
    if (grant) return { room: await liveApi.state(grant), grant }
    if (invite) return { room: await liveApi.invitation(roomId, invite), grant: null }
    const session = await api.session()
    if (!session) throw new Error('INVITATION_REQUIRED')
    const hostGrant = await liveApi.host(roomId)
    saveGrant(hostGrant)
    return { room: await liveApi.state(hostGrant), grant: hostGrant }
  } })
  const effectiveGrant = grant ?? entry.data?.grant ?? null
  const room = useQuery({ queryKey: ['live', 'room', roomId], enabled: joined && !!effectiveGrant,
    queryFn: () => liveApi.state(effectiveGrant!), staleTime: 0, retry: false, refetchInterval: 1500 })
  function join(value: LiveGrant) { setGrant(value); setJoined(true) }
  const frame = (content: ReactNode) => effectiveGrant?.is_host ? <WorkspaceFrame>{content}</WorkspaceFrame> : content
  const accessFailed = room.error instanceof RoomAccessError || (room.error instanceof ApiError && [403, 404].includes(room.error.status))
  if (joined && effectiveGrant && room.data && !accessFailed) return frame(<RoomWorkspace grant={effectiveGrant} data={room.data} view={view} focus={focus} syncFailed={room.isError} onRetrySync={() => void room.refetch()}
    onMode={(mode, target) => { void navigate({ to: '/live/$roomId', params: { roomId }, search: { view: mode, focus: target }, replace: true }) }}
    onLeave={() => { setJoined(false) }} />)
  const error = room.error ?? entry.error
  return frame(<div className="live-room-shell">{!effectiveGrant?.is_host && <header className="live-topbar"><Brand /><LanguageSwitcher /></header>}<div>
    {error ? <div className="live-prejoin"><ErrorState title={t("Не удалось открыть комнату")} description={error instanceof Error && error.message === 'INVITATION_REQUIRED' ? t("Откройте ссылку-приглашение от организатора или войдите в рабочее пространство.") : liveError(error)} onRetry={() => { void entry.refetch(); if (joined) void room.refetch() }}><Button variant="default" component={Link} to="/login">{t("Войти в рабочее пространство")}</Button></ErrorState></div>
      : entry.isPending || (joined && room.isPending) ? <LiveConnecting title={joined ? t("Открываем комнату") : t("Готовим встречу")} description={t("Загружаем пространство разговора")} />
      : entry.data ? <RoomPrejoin room={entry.data.room} grant={effectiveGrant} invite={invite} onJoin={join} /> : null}
    <div className="live-prejoin-footer"><Anchor component={Link} to="/live">{t("К голосовым встречам")}</Anchor></div>
  </div></div>)
}
