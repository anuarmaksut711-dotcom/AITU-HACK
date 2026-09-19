import { useSyncExternalStore } from 'react'
import { z } from 'zod'
import ru from './ru.json'
import en from './en.json'
import kk from './kk.json'

export type Locale = 'ru' | 'kk' | 'en'
export type MessageKey = keyof typeof ru
export const localeNames: Record<Locale, string> = { ru: 'Русский', kk: 'Қазақша', en: 'English' }
export const catalogs: Record<Locale, Record<MessageKey, string>> = { ru, kk, en }
const storageKey = 'soyle-locale'
const listeners = new Set<() => void>()
export function isLocale(value: unknown): value is Locale { return value === 'ru' || value === 'kk' || value === 'en' }
function initialLocale(): Locale {
  try { const stored = localStorage.getItem(storageKey); if (isLocale(stored)) return stored } catch { /* Storage is optional. */ }
  if (typeof navigator !== 'undefined') {
    for (const language of navigator.languages ?? [navigator.language]) {
      const candidate = language.toLowerCase().split('-')[0]
      if (isLocale(candidate)) return candidate
    }
  }
  return 'ru'
}
let locale = initialLocale()
export function getLocale(): Locale { return locale }
export function setLocale(next: Locale) {
  if (!isLocale(next)) return
  try { localStorage.setItem(storageKey, next) } catch { /* Keep the choice for this session. */ }
  if (locale === next) return
  locale = next
  if (typeof document !== 'undefined') document.documentElement.lang = next
  listeners.forEach((listener) => listener())
}
if (typeof document !== 'undefined') document.documentElement.lang = locale
if (typeof window !== 'undefined') window.addEventListener('storage', (event) => {
  if (event.key === storageKey) {
    const next = isLocale(event.newValue) ? event.newValue : initialLocale()
    if (next !== locale) { locale = next; document.documentElement.lang = next; listeners.forEach((listener) => listener()) }
  }
})
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener) } }
export function useLocale() { return useSyncExternalStore(subscribe, getLocale, () => 'ru' as Locale) }
export function translate(language: Locale, key: MessageKey, values: Record<string, unknown> = {}): string {
  return catalogs[language][key].replace(/\{(\w+)\}/g, (placeholder, name: string) => Object.hasOwn(values, name) ? String(values[name] ?? '') : placeholder)
}
export function t(key: MessageKey, values?: Record<string, unknown>) { return translate(locale, key, values) }
/** Only for application-owned messages retained in form state or async jobs. Never apply to user content. */
export function localizeMessage(message: string | undefined): string | undefined {
  if (!message) return message
  if (Object.hasOwn(ru, message)) return t(message as MessageKey)
  for (const language of ['en', 'kk'] as const) {
    const key = (Object.keys(catalogs[language]) as MessageKey[]).find((key) => catalogs[language][key] === message)
    if (key) return t(key)
  }
  return message
}
export function formatNumber(value: number) { return new Intl.NumberFormat(locale).format(value) }
export function formatDate(value: string | Date, options: Intl.DateTimeFormatOptions = { dateStyle: 'medium' }) {
  // Date-only values are calendar dates, not UTC instants.
  const date = typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00`) : new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : new Intl.DateTimeFormat(locale, options).format(date)
}

// Schema-specific messages take precedence; otherwise avoid Zod's English defaults.
z.config({ customError: (issue) => {
  if (issue.code === 'too_big' && issue.origin === 'string') return t('Не более {count} символов', { count: formatNumber(Number(issue.maximum)) })
  if (issue.code === 'too_small' && issue.origin === 'string') return t('Не менее {count} символов', { count: formatNumber(Number(issue.minimum)) })
  return t('Проверьте значение поля')
} })
