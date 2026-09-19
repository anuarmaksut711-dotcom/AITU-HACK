import { useEffect, type ReactNode } from 'react'
import { MantineProvider, Modal } from '@mantine/core'
import { cssVariablesResolver, theme } from '../theme'
import { t, useLocale } from '../i18n'

export function LocalizedProvider({ children, nonce }: { children: ReactNode; nonce: string }) {
  const locale = useLocale()
  useEffect(() => {
    document.documentElement.lang = locale
    document.querySelector<HTMLMetaElement>('meta[name="description"]')?.setAttribute('content', t('Soyle — записи встреч, стенограммы и ответы на вопросы по вашим разговорам.'))
  }, [locale])
  return <MantineProvider theme={{ ...theme, components: {
    ...theme.components,
    Modal: Modal.extend({ defaultProps: { closeButtonProps: { 'aria-label': t('Закрыть') } } }),
  } }} cssVariablesResolver={cssVariablesResolver} forceColorScheme="light" getStyleNonce={() => nonce}>
    {children}
  </MantineProvider>
}
