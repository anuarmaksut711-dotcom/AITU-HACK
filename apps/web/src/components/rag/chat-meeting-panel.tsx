import { useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Accordion, Anchor, Drawer, Group, Text } from '@mantine/core'
import { ArrowUpRight, CalendarDays } from 'lucide-react'
import { useMediaQuery, useViewportSize } from '@mantine/hooks'
import { t, useLocale } from '../../i18n'
import { meetingQuery } from '../../lib/query'
import { errorMessage } from '../../lib/api'
import { ErrorState, LoadingState } from '../ui'
import { MeetingWorkspace, type MeetingView } from '../audio-workspace'

export type ChatMeetingSelection = {
  meetingId: string
  title: string
  view: MeetingView
  quotes: string[]
  provisional?: boolean
}

export function ChatMeetingPanel({ selection, onClose }: { selection: ChatMeetingSelection; onClose: () => void }) {
  useLocale()
  const mobile = useMediaQuery('(max-width: 760px)')
  const { width: viewportWidth } = useViewportSize()
  const maxWidth = Math.max(320, (viewportWidth || window.innerWidth) - 32)
  const minWidth = Math.min(480, maxWidth)
  const [panelWidth, setPanelWidth] = useState(() => Math.min(1100, window.innerWidth * 0.72))
  const width = Math.round(Math.max(minWidth, Math.min(maxWidth, panelWidth)))
  const drag = useRef<{ x: number; width: number } | null>(null)
  const resize = (value: number) => setPanelWidth(Math.max(minWidth, Math.min(maxWidth, value)))
  const [view, setView] = useState(selection.view)
  const meeting = useQuery(meetingQuery(selection.meetingId))
  return <Drawer opened onClose={onClose} position="right" size={mobile ? '100%' : width}
    classNames={{ content: 'chat-meeting-panel', header: 'chat-meeting-panel-header', body: 'chat-meeting-panel-body' }}
    title={<Group gap="sm" wrap="nowrap"><CalendarDays size={22} aria-hidden="true" /><Text fw={600} lineClamp={2}>{meeting.data?.title ?? selection.title}</Text></Group>}
    closeButtonProps={{ 'aria-label': t('Закрыть панель встречи') }} overlayProps={{ backgroundOpacity: 0.18, blur: 1 }}>
    {!mobile && <div className="chat-panel-resize" role="separator" aria-orientation="vertical" tabIndex={0}
      aria-label={t('Ширина панели')} aria-valuemin={minWidth} aria-valuemax={maxWidth} aria-valuenow={width}
      onPointerDown={(event) => {
        if (event.button !== 0) return
        event.preventDefault()
        drag.current = { x: event.clientX, width }
        event.currentTarget.setPointerCapture(event.pointerId)
      }}
      onPointerMove={(event) => { if (drag.current) resize(drag.current.width + drag.current.x - event.clientX) }}
      onPointerUp={(event) => { drag.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId) }}
      onPointerCancel={() => { drag.current = null }} onLostPointerCapture={() => { drag.current = null }}
      onKeyDown={(event) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
        event.preventDefault()
        resize(event.key === 'Home' ? minWidth : event.key === 'End' ? maxWidth : width + (event.key === 'ArrowLeft' ? 40 : -40))
      }} />}
    {meeting.isPending ? <LoadingState /> : meeting.isError ? <ErrorState description={errorMessage(meeting.error)} onRetry={() => void meeting.refetch()} /> : <>
      <div className="chat-meeting-panel-context">
        <Anchor className="chat-meeting-full-link" renderRoot={(props) => <Link {...props} to="/meetings/$meetingId" params={{ meetingId: selection.meetingId }} search={{ view }} />}>
          {t('Открыть встречу')}<ArrowUpRight size={15} aria-hidden="true" />
        </Anchor>
        {selection.quotes.length > 0 && <Accordion variant="separated" radius="md">
          <Accordion.Item value="citations"><Accordion.Control>{t('Цитаты из ответа')} · {selection.quotes.length}</Accordion.Control>
            <Accordion.Panel>
              {selection.provisional && <Text size="sm" c="dimmed" mb="sm">{t('Промежуточные итоги — ещё могут измениться.')}</Text>}
              {selection.quotes.map((quote, index) => <blockquote className="chat-meeting-quote" key={index}>{quote}</blockquote>)}
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>}
      </div>
      <MeetingWorkspace meeting={meeting.data} view={view} onViewChange={setView} />
    </>}
  </Drawer>
}
