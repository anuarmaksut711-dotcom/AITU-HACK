import { useQuery } from '@tanstack/react-query'
import { z } from 'zod'
import { request } from './api'
import type { MeetingDetail } from './contracts'
import type { Evidence } from './board'

const clipSchema = z.object({
  id: z.uuid(), speaker: z.string(), start: z.number(), end: z.number(),
  start_char: z.number().int(), end_char: z.number().int(), available: z.boolean(),
})
const audioSchema = z.object({
  source: z.enum(['live', 'none']), clips: z.array(clipSchema), full_audio_available: z.boolean(),
  recording_status: z.enum(['none', 'recording', 'queued', 'processing', 'ready', 'failed']),
})
export type MeetingAudioClip = z.infer<typeof clipSchema>
export function useMeetingAudio(meeting: MeetingDetail) {
  return useQuery({
    queryKey: ['meeting-audio', meeting.id],
    queryFn: ({ signal }) => request(`/meetings/${encodeURIComponent(meeting.id)}/audio-clips`, audioSchema, { signal }),
    staleTime: 15000,
    refetchInterval: (query) => ['queued', 'processing', 'recording'].includes(query.state.data?.recording_status ?? '') ? 2000 : false,
    retry: false,
  })
}
export function clipsForEvidence(clips: MeetingAudioClip[], evidence: Evidence | null) {
  if (!evidence) return []
  return clips.filter((clip) => clip.start_char < evidence.end_char && clip.end_char > evidence.start_char)
}
export function clipAudioUrl(meetingId: string, clipId: string) {
  return `/api/v1/meetings/${encodeURIComponent(meetingId)}/audio-clips/${encodeURIComponent(clipId)}`
}
export function retryMeetingRecording(meetingId: string) {
  return request(`/meetings/${encodeURIComponent(meetingId)}/audio-recording/retry`, z.object({ status: z.literal('queued') }), { method: 'POST' })
}
