import { type KeyboardCoordinateGetter, type CollisionDetection, pointerWithin, closestCenter } from '@dnd-kit/core'

// A pointer released outside every column must not choose the closest column.
export const boardCollision: CollisionDetection = (args) => args.pointerCoordinates ? pointerWithin(args) : closestCenter(args)

// One arrow selects one column, including columns stacked on a narrow viewport.
export const boardKeyboardCoordinates: KeyboardCoordinateGetter = (event, { context }) => {
  const direction = ['ArrowRight', 'ArrowDown'].includes(event.code) ? 1 : ['ArrowLeft', 'ArrowUp'].includes(event.code) ? -1 : 0
  if (!direction || !context.active) return undefined
  event.preventDefault()
  const columns = context.droppableContainers.getEnabled().sort((a, b) => a.data.current!.index - b.data.current!.index)
  const current = context.over?.id ?? context.active.data.current?.columnId
  const index = columns.findIndex((column) => column.id === current)
  const next = columns[index + direction]
  const rect = next && context.droppableRects.get(next.id)
  if (!rect) return undefined
  return { x: rect.left + 10, y: rect.top + 48 }
}

