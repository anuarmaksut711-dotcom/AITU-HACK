import { t } from '../i18n'
import { z } from 'zod'
import { ApiError, errorMessage, request } from './api'

export const roomSchema = z.object({
  id: z.uuid(), title: z.string(), language: z.string(),
  status: z.enum(['active', 'ending', 'ended']), created_at: z.string(),
  ended_at: z.string().nullable(), meeting_id: z.uuid().nullable(),
})
export const grantSchema = z.object({
  room_id: z.uuid(), participant_id: z.uuid(), name: z.string(), is_host: z.boolean(),
  member_token: z.string(), media_token: z.string(), media_url: z.string(),
})
export const utteranceSchema = z.object({
  id: z.uuid(), participant_id: z.uuid(), speaker: z.string(),
  start: z.number(), end: z.number(), text: z.string(),
})
export const insightSchema = z.object({
  kind: z.enum(['goal', 'idea', 'decision', 'task', 'question']), text: z.string(),
  source_ids: z.array(z.string()), quotes: z.array(z.string()),
})
export const stateSchema = roomSchema.extend({
  participants: z.array(z.object({ id: z.uuid(), name: z.string(), is_host: z.boolean() })),
  utterances: z.array(utteranceSchema), insights: z.array(insightSchema),
  transcript_revision: z.number(), analysis_status: z.string(), analysis_error: z.string().nullable(),
  audio_error: z.string().nullable(), analysis_provider: z.string(), can_end: z.boolean(),
})
export type LiveGrant = z.infer<typeof grantSchema>
export type LiveRoom = z.infer<typeof roomSchema>
export type LiveState = z.infer<typeof stateSchema>
export type Utterance = z.infer<typeof utteranceSchema>
export const liveSearch = z.object({ view: z.enum(['conversation', 'insights']).catch('insights'), focus: z.uuid().optional().catch(undefined) })

export function saveGrant(grant: LiveGrant, autoJoin = false) {
  sessionStorage.setItem(`soyle-live:${grant.room_id}`, JSON.stringify(grant))
  if (autoJoin) sessionStorage.setItem(`soyle-live-autojoin:${grant.room_id}`, '1')
}
export function storedGrant(id: string): LiveGrant | null {
  try {
    const parsed = grantSchema.safeParse(JSON.parse(sessionStorage.getItem(`soyle-live:${id}`) ?? 'null'))
    return parsed.success ? parsed.data : null
  } catch { return null }
}
export function consumeAutoJoin(id: string) {
  const value = sessionStorage.getItem(`soyle-live-autojoin:${id}`) === '1'
  sessionStorage.removeItem(`soyle-live-autojoin:${id}`)
  return value
}
export class RoomAccessError extends Error {}
export function transcriptRetryDelay(error: unknown, failures: number): number | false {
  if (error instanceof RoomAccessError) return false
  if (error instanceof ApiError && error.status < 500 && ![408, 429].includes(error.status)) return false
  return Math.min(30000, 5000 * 2 ** Math.min(3, Math.max(0, failures - 1)))
}
async function memberRequest<T>(grant: LiveGrant, suffix: string, schema: z.ZodType<T>, method = 'GET', signal?: AbortSignal): Promise<T> {
  try {
    return await request(`/live/rooms/${grant.room_id}${suffix}`, schema, {
      method, signal, headers: { Authorization: `Bearer ${grant.member_token}` },
    })
  } catch (error) {
    // A room grant expiring must not log the account out of the unrelated archive.
    if (error instanceof ApiError && error.status === 401) throw new RoomAccessError()
    throw error
  }
}
export const liveApi = {
  list: () => request('/live/rooms', z.array(roomSchema)),
  create: (values: { title: string; language: string }) => request('/live/rooms', grantSchema, { method: 'POST', body: JSON.stringify(values) }),
  host: (id: string) => request(`/live/rooms/${encodeURIComponent(id)}/host`, grantSchema, { method: 'POST' }),
  invitation: (id: string, invite: string) => request(`/live/rooms/${encodeURIComponent(id)}/invitation`, roomSchema, { method: 'POST', body: JSON.stringify({ invite }) }),
  join: (id: string, invite: string, name: string) => request(`/live/rooms/${encodeURIComponent(id)}/join`, grantSchema, { method: 'POST', body: JSON.stringify({ invite, name }) }),
  state: (grant: LiveGrant) => memberRequest(grant, '', stateSchema),
  refresh: (grant: LiveGrant) => memberRequest(grant, '/token', grantSchema, 'POST'),
  invite: (grant: LiveGrant) => memberRequest(grant, '/invite', z.object({ invite: z.string() })),
  end: (grant: LiveGrant) => memberRequest(grant, '/end', z.object({ status: z.string() }), 'POST'),
  retryAnalysis: (grant: LiveGrant) => memberRequest(grant, '/analysis/retry', z.object({ status: z.string() }), 'POST'),
  transcript: (grant: LiveGrant, offset: number, signal?: AbortSignal) => memberRequest(grant, `/transcript?offset=${offset}`, z.array(utteranceSchema), 'GET', signal),
}
export function liveError(error: unknown) {
  if (error instanceof RoomAccessError) return t("Доступ к комнате истёк. Откройте приглашение и войдите снова.")
  if (error instanceof ApiError && error.status === 404) return t("Комната или приглашение не найдены.")
  if (error instanceof ApiError && error.status === 409) return t("Войти сейчас не удалось: встреча завершена или в комнате нет свободных мест.")
  if (error instanceof ApiError && error.status === 503) return t("Голосовая связь пока недоступна. Попробуйте ещё раз.")
  return errorMessage(error, t("Не удалось выполнить действие. Попробуйте ещё раз."))
}
export function formatTime(seconds: number) {
  const value = Math.max(0, Math.floor(seconds))
  return `${Math.floor(value / 60).toString().padStart(2, '0')}:${(value % 60).toString().padStart(2, '0')}`
}
export function mediaUrl(value: string) {
  const url = new URL(value, window.location.origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : url.protocol === 'http:' ? 'ws:' : url.protocol
  return url.toString().replace(/\/$/, '')
}

export async function captureMicrophone(track: MediaStreamTrack, grant: LiveGrant, onError: (message: string) => void) {
  const url = new URL(`/api/v1/live/rooms/${grant.room_id}/audio`, window.location.origin)
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const socket = new WebSocket(url, ['soyle-live', grant.member_token])
  let stopping = false
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => { socket.close(); reject(new Error('Audio stream timeout')) }, 8000)
    socket.onopen = () => { clearTimeout(timer); resolve() }
    socket.onerror = () => { clearTimeout(timer); reject(new Error('Audio stream unavailable')) }
  })
  const context = new AudioContext({ sampleRate: 16000 })
  try {
    await context.audioWorklet.addModule('/api/v1/live/audio-processor.js')
    const source = context.createMediaStreamSource(new MediaStream([track]))
    const worklet = new AudioWorkletNode(context, 'soyle-pcm')
    const silent = context.createGain()
    silent.gain.value = 0
    source.connect(worklet).connect(silent).connect(context.destination)
    worklet.port.onmessage = (event: MessageEvent<ArrayBuffer | string>) => {
      if (event.data instanceof ArrayBuffer && socket.readyState === WebSocket.OPEN) {
        if (socket.bufferedAmount > 128000) {
          onError(t("Распознавание не успевает за связью. Переподключите микрофон."))
          socket.close()
        } else socket.send(event.data)
      }
    }
    socket.onclose = () => { if (!stopping) onError(t("Поток распознавания прервался. Переподключите микрофон.")) }
    socket.onmessage = () => onError(t("Распознавание перегружено. Переподключите микрофон немного позже."))
    await context.resume()
    return async () => {
      if (stopping) return
      stopping = true
      worklet.port.postMessage('flush')
      await new Promise((resolve) => setTimeout(resolve, 100))
      source.disconnect(); worklet.disconnect(); silent.disconnect()
      if (socket.readyState === WebSocket.OPEN) socket.send('stop')
      socket.close()
      await context.close()
    }
  } catch (error) {
    stopping = true
    socket.close()
    await context.close()
    throw error
  }
}
