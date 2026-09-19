import { t, useLocale } from '../i18n'
import { Link } from '@tanstack/react-router'
import { Button } from '@mantine/core'
import { LanguageSwitcher } from './language-switcher'
import { PageHeading, ErrorState } from './ui'

export function RouteError({ reset }: { reset: () => void }) {
  useLocale()
  return <main className="main-content"><div className="route-language"><LanguageSwitcher /></div><PageHeading title={t('Не удалось открыть страницу. Попробуйте ещё раз.')} /><ErrorState description={t('Не удалось открыть страницу. Попробуйте ещё раз.')} onRetry={reset} /></main>
}
export function NotFound() {
  useLocale()
  return <main className="main-content"><div className="route-language"><LanguageSwitcher /></div><PageHeading title={t('Страница не найдена')} /><ErrorState title={t('Страница не найдена')} description={t('Вернитесь к списку встреч.')}><Button renderRoot={(props) => <Link {...props} to="/meetings" search={{ q: '', offset: 0 }} />}>{t('К встречам')}</Button></ErrorState></main>
}
