import { Select } from '@mantine/core'
import { isLocale, localeNames, setLocale, t, useLocale, type Locale } from '../i18n'

export function LanguageSwitcher({ compact = false }: { compact?: boolean }) {
  const locale = useLocale()
  const shortNames: Record<Locale, string> = { ru: 'RU', kk: 'ҚАЗ', en: 'EN' }
  return <Select className={`language-switcher${compact ? ' is-compact' : ''}`} value={locale}
    size="sm" allowDeselect={false} aria-label={t('Язык интерфейса')} title={t('Язык интерфейса')}
    data={(['ru', 'kk', 'en'] as const).map((value) => ({ value, label: compact ? shortNames[value] : localeNames[value] }))}
    comboboxProps={{ position: 'bottom-end', width: compact ? 140 : undefined }}
    onChange={(value) => { if (isLocale(value)) setLocale(value) }} />
}
