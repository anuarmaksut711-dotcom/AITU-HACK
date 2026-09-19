import { t, useLocale } from '../../i18n'
import type { ReactNode } from 'react'
import { ActionIcon } from '@mantine/core'
import { GripVertical } from 'lucide-react'
import { useDraggable, useDroppable } from '@dnd-kit/core'
import type { Card } from '../../lib/board'

export function BoardColumn({ id, index, label, className, disabled, children }: {
  id: string; index: number; label: string; className: string; disabled: boolean; children: ReactNode
}) {
  useLocale()
  const { setNodeRef, isOver, active } = useDroppable({ id, disabled, data: { index, label } })
  const target = isOver && active?.data.current?.columnId !== id
  return <section ref={setNodeRef} className={`${className} ${target ? 'drop-target' : ''}`} aria-label={label} data-drop-column={id}>
    {children}
    {target && <div className="board-drop-hint" aria-hidden="true">{t("Переместить сюда")}</div>}
  </section>
}

export function DraggableCard({ card, columnId, disabled, children }: {
  card: Card; columnId: string; disabled: boolean; children: (handle: ReactNode) => ReactNode
}) {
  useLocale()
  const { setNodeRef, setActivatorNodeRef, listeners, attributes, isDragging } = useDraggable({
    id: card.id, data: { columnId, title: card.title }, disabled,
    attributes: { roleDescription: t("перетаскиваемая карточка") },
  })
  const handle = <ActionIcon ref={setActivatorNodeRef} {...attributes} {...listeners} disabled={disabled}
    className="card-drag-handle" variant="subtle" color="gray" size="sm"
    aria-label={t("Переместить: {0}", { "0": card.title })} title={t("Перетащить карточку")} onClick={(event) => event.stopPropagation()}>
    <GripVertical size={17} aria-hidden="true" />
  </ActionIcon>
  return <article ref={setNodeRef} className={`kanban-card ${isDragging ? 'card-dragging' : ''}`} data-card-id={card.id}>
    {children(handle)}
  </article>
}
