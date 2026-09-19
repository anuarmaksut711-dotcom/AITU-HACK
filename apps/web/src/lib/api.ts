import { t } from '../i18n'
import type { z } from 'zod'
import {
  userSchema, meetingListSchema, meetingDetailSchema,
  type LoginInput, type MeetingCreate,
} from './contracts'

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly kind = 'request') {
    super(`API request failed (${status}, ${kind})`)
    this.name = 'ApiError'
  }
}

const baseUrl = '/api/v1'
export async function request<T>(path: string, schema: z.ZodType<T>, options?: RequestInit): Promise<T>
export async function request(path: string, schema: null, options?: RequestInit): Promise<void>
export async function request<T>(path: string, schema: z.ZodType<T> | null, options: RequestInit = {}): Promise<T | void> {
  const method = options.method ?? 'GET'
  const headers = new Headers(options.headers)
  headers.set('Accept', 'application/json')
  if (method !== 'GET') headers.set('X-Requested-With', 'aimeet')
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${baseUrl}${path}`, {
    ...options, method, headers, credentials: 'include', cache: 'no-store',
  })
  if (!response.ok) throw new ApiError(response.status)
  if (schema === null) return
  let body: unknown
  try { body = await response.json() } catch { throw new ApiError(response.status, 'invalid-response') }
  const decoded = schema.safeParse(body)
  if (!decoded.success) throw new ApiError(response.status, 'invalid-response')
  return decoded.data
}

export const api = {
  async session(signal?: AbortSignal) {
    try { return await request('/auth/me', userSchema, { signal }) }
    catch (error) { if (error instanceof ApiError && error.status === 401) return null; throw error }
  },
  login: (values: LoginInput) => request('/auth/login', userSchema, { method: 'POST', body: JSON.stringify(values) }),
  logout: () => request('/auth/logout', null, { method: 'POST' }),
  meetings: (q: string, offset: number, signal?: AbortSignal) => request(
    `/meetings?${new URLSearchParams({ q, offset: String(offset), limit: '20' })}`,
    meetingListSchema, { signal },
  ),
  meeting: (id: string, signal?: AbortSignal) => request(`/meetings/${encodeURIComponent(id)}`, meetingDetailSchema, { signal }),
  createMeeting: (values: MeetingCreate) => request('/meetings', meetingDetailSchema, { method: 'POST', body: JSON.stringify(values) }),
  uploadAudio: (values: { title: string; language: string; file: File }) => request(
    `/meetings/audio?${new URLSearchParams({ title: values.title, language: values.language, filename: values.file.name })}`,
    meetingDetailSchema, { method: 'POST', body: values.file, headers: { 'Content-Type': 'application/octet-stream' } },
  ),
  cancelTranscription: (id: string) => request(`/meetings/${encodeURIComponent(id)}/transcription/cancel`, meetingDetailSchema, { method: 'POST' }),
  retryTranscription: (id: string) => request(`/meetings/${encodeURIComponent(id)}/transcription/retry`, meetingDetailSchema, { method: 'POST' }),
  deleteMeeting: (id: string) => request(`/meetings/${encodeURIComponent(id)}`, null, { method: 'DELETE' }),
}

export function errorMessage(error: unknown, fallback = t("Не удалось загрузить данные. Попробуйте ещё раз.")): string {
  if (!(error instanceof ApiError)) return fallback
  if (error.status === 401) return t("Войдите в рабочее пространство ещё раз.")
  if (error.status === 403) return t("Недостаточно прав для этого действия.")
  if (error.status === 404) return t("Встреча не найдена. Возможно, её уже удалили.")
  if (error.status === 429) return t("Слишком много попыток. Попробуйте немного позже.")
  if (error.status === 413) return t("Превышен размер загрузки: до 100 МБ для аудио или 200 000 символов для текста.")
  if (error.status === 415) return t("Выберите аудиофайл в формате MP3, WAV или M4A.")
  if (error.status === 409) return t("Действие сейчас недоступно. Обновите встречу и попробуйте ещё раз.")
  if (error.status === 422) return t("Проверьте заполненные поля и повторите попытку.")
  return fallback
}
