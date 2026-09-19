import { t, useLocale } from '../i18n'
import { useEffect, useRef, type ReactNode } from 'react'
import { CircleAlert, FileText } from 'lucide-react'
import { Accordion, Alert, Button, Skeleton, Text, Title, VisuallyHidden } from '@mantine/core'
import { brand, pageTitle } from '../brand'

export function Brand() {
  useLocale()
  return <span className="brand"><img className="brand-mark" src={brand.mark} width={40} height={40} alt="" aria-hidden="true" /><span className="brand-name">{brand.name}</span></span>
}

export function PageHeading({ title, children }: { title: string; children?: ReactNode }) {
  useLocale()
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    document.title = pageTitle(title)
    heading.current?.focus({ preventScroll: true })
  }, [title])
  return <div className="page-heading"><Title order={1} ref={heading} tabIndex={-1}>{title}</Title>{children}</div>
}

export function LoadingState() {
  useLocale()
  return <div className="page-loading" role="status" aria-label={t("Загрузка")}>
    <VisuallyHidden>{t("Загружаем рабочее пространство…")}</VisuallyHidden>
    <Skeleton className="skeleton-heading" />
    <Skeleton className="skeleton-row" /><Skeleton className="skeleton-row" /><Skeleton className="skeleton-row" />
  </div>
}

export function ErrorState({ title = t("Не удалось загрузить данные"), description, onRetry, children }: {
  title?: string; description: string; onRetry?: () => void; children?: ReactNode;
}) {
  useLocale()
  return <Alert color="red" icon={<CircleAlert size={22} aria-hidden="true" />} title={title} role="alert" className="error-state">
    <Text size="sm">{description}</Text>
    <div className="form-actions">
      {onRetry && <Button variant="default" onClick={onRetry}>{t("Попробовать снова")}</Button>}
      {children}
    </div>
  </Alert>
}

export function InlineError({ children }: { children: ReactNode }) {
  useLocale()
  return <Alert color="red" icon={<CircleAlert size={18} aria-hidden="true" />} role="alert" className="inline-error">{children}</Alert>
}

export function EmptyState({ title, description, children }: { title: string; description: string; children?: ReactNode }) {
  useLocale()
  return <section className="empty-state">
    <div className="empty-icon"><FileText size={30} strokeWidth={1.5} aria-hidden="true" /></div>
    <Title order={2} size="h3">{title}</Title><Text size="sm" c="dimmed" className="empty-description">{description}</Text>{children}
  </section>
}

export function Disclosure({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  useLocale()
  return <Accordion variant="separated" className={className}>
    <Accordion.Item value="content">
      <Accordion.Control>{label}</Accordion.Control>
      <Accordion.Panel>{children}</Accordion.Panel>
    </Accordion.Item>
  </Accordion>
}
