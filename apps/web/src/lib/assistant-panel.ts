import type { AssistantResult } from './rag'
import type { ChatMeetingSelection } from '../components/rag/chat-meeting-panel'

/** Execute only a typed model action with a target found in the validated result. */
export function assistantPanel(result: AssistantResult): ChatMeetingSelection | null {
  if (result.mode === 'assistant' || !result.panel) return null
  const { meeting_id: meetingId, view } = result.panel
  if (result.mode === 'navigation') {
    const target = result.meetings.find((meeting) => meeting.id === meetingId)
    return target ? { meetingId, title: target.title, view, quotes: [] } : null
  }
  if (result.mode === 'tasks') {
    if (result.status !== 'created') return null
    const task = result.tasks.find((task) => task.meeting_id === meetingId)
    return task ? { meetingId, title: task.meeting_title, view, quotes: [] } : null
  }
  const sources = [...result.board_sources, ...result.catalog_sources, ...result.sources]
    .filter((source) => source.meeting_id === meetingId)
  const target = sources.find((source) => source.meeting_title)
  if (!target?.meeting_title) return null
  const sourceIds = new Set(sources.map((source) => source.source_id))
  const quotes = [...new Set(result.claims.flatMap((claim) => claim.citations
    .filter((citation) => sourceIds.has(citation.source_id)).map((citation) => citation.quote)))]
  return { meetingId, title: target.meeting_title, view, quotes,
    provisional: result.board_sources.some((source) => source.meeting_id === meetingId && source.provisional) }
}
