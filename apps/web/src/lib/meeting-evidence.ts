import type { Evidence } from './board'
import type { MeetingDetail } from './contracts'

// API offsets count Unicode characters, not JavaScript UTF-16 code units.
export function resolveMeetingEvidence(meeting: MeetingDetail, quote: string, evidence?: Evidence | null, startHint?: number | null): Evidence | null {
  const characters = Array.from(meeting.transcript)
  const size = Array.from(quote).length
  if (!size) return null
  let reference: Evidence
  if (evidence && characters.slice(evidence.start_char, evidence.end_char).join('') === quote && evidence.quote === quote) {
    reference = { ...evidence }
  } else if (startHint !== null && startHint !== undefined && characters.slice(startHint, startHint + size).join('') === quote) {
    reference = { quote, start_char: startHint, end_char: startHint + size, start_seconds: null, end_seconds: null, speaker: null }
  } else {
    const position = meeting.transcript.indexOf(quote)
    if (position < 0 || meeting.transcript.indexOf(quote, position + 1) >= 0) return null
    const start = Array.from(meeting.transcript.slice(0, position)).length
    reference = { quote, start_char: start, end_char: start + size, start_seconds: null, end_seconds: null, speaker: null }
  }
  // Older API versions omitted timing for archived Live transcripts. Derive it
  // only when the saved segments reconstruct the source exactly.
  const ranges = transcriptSegmentRanges(meeting)
  const matching = (meeting.segments ?? []).filter((_, i) => ranges[i] && ranges[i].start < reference.end_char && ranges[i].end > reference.start_char)
  if (matching.length) {
    reference.start_seconds = matching[0].start
    reference.end_seconds = matching.at(-1)!.end
    const labels = matching.map((segment) => /^([^:\n]{1,80}):\s/u.exec(segment.text)?.[1])
    if (labels.every((name) => name && name === labels[0])) reference.speaker = labels[0] ?? null
  }
  return reference
}

export function transcriptSegmentRanges(meeting: MeetingDetail) {
  const segments = meeting.segments ?? []
  const plain = segments.map((s) => s.text)
  const prefixes = plain.join('\n') === meeting.transcript ? segments.map(() => '') : segments.map((s) => `[${String(Math.floor(s.start / 60)).padStart(2, '0')}:${String(Math.floor(s.start) % 60).padStart(2, '0')}] `)
  if (segments.map((s, i) => prefixes[i] + s.text).join('\n') !== meeting.transcript) return []
  let offset = 0
  return segments.map((segment, i) => {
    const start = offset + Array.from(prefixes[i]).length
    const end = start + Array.from(segment.text).length
    offset = end + 1
    return { start, end }
  })
}
