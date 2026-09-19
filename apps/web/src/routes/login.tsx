import { t, useLocale, localizeMessage } from '../i18n'
import { useEffect, useState } from 'react'
import { Navigate } from '@tanstack/react-router'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { Button, PasswordInput, Text, TextInput, Title } from '@mantine/core'
import { ArrowRight } from 'lucide-react'
import { api, ApiError, errorMessage } from '../lib/api'
import { loginSchema, type LoginInput } from '../lib/contracts'
import { queryClient, sessionQuery } from '../lib/query'
import { LanguageSwitcher } from '../components/language-switcher'
import { Brand, InlineError } from '../components/ui'
import { brand, pageTitle } from '../brand'

const waveformHeights = [8, 15, 25, 17, 39, 64, 92, 122, 79, 53, 72, 40, 26, 35, 21, 13, 8]

function ConversationIllustration() {
  useLocale()
  return <div className="login-memory" role="group" aria-label={t("От записи к ответам")}>
    <svg className="login-memory-thread" viewBox="0 0 640 210" fill="none" preserveAspectRatio="none" aria-hidden="true">
      <path d="M-20 136 C35 135 55 90 115 105 S174 145 207 107 S246 48 282 95 S354 163 387 126 S426 63 459 102 S533 139 564 98 S617 65 660 77" />
    </svg>
    <div className="login-memory-step">
      <div className="login-audio-art" aria-hidden="true">
        <svg viewBox="0 0 210 180" className="login-waveform" fill="none">
          {waveformHeights.map((height, index) => <rect key={index} x={12 + index * 11} y={(180 - height) / 2} width="5" height={height} rx="2.5" />)}
        </svg>
      </div>
      <span>{t("Аудио")}</span>
    </div>
    <div className="login-memory-step">
      <div className="login-transcript-art" aria-hidden="true">
        <div className="login-paper-sheet"><i /><i /><i /><i /><i /><i /><i /><i /></div>
      </div>
      <span>{t("Стенограмма")}</span>
    </div>
    <div className="login-memory-step">
      <div className="login-answer-art" aria-hidden="true">
        <div className="login-answer-sheet">
          <svg viewBox="0 0 32 32" width="25" height="25" fill="currentColor"><path d="M16 2c1.5 9.5 4.5 12.5 14 14-9.5 1.5-12.5 4.5-14 14C14.5 20.5 11.5 17.5 2 16 11.5 14.5 14.5 11.5 16 2Z" /></svg>
          <div className="login-answer-bubble"><i /><i /></div>
        </div>
      </div>
      <span>{t("Ответы")}</span>
    </div>
  </div>
}

export function LoginPage() {
  const locale = useLocale()
  useEffect(() => { document.title = pageTitle(t("Вход")) }, [locale])
  const [passwordVisible, setPasswordVisible] = useState(false)
  const session = useQuery(sessionQuery)
  const form = useForm<LoginInput>({ resolver: zodResolver(loginSchema), defaultValues: { email: '', password: '' } })
  const login = useMutation({
    mutationFn: api.login,
    onSuccess: (user) => { queryClient.clear(); queryClient.setQueryData(['session'], user) },
  })
  if (session.data) return <Navigate to="/meetings" search={{ q: '', offset: 0 }} replace />
  const loginError = login.error instanceof ApiError && login.error.status === 401
    ? t("Проверьте электронную почту и пароль.")
    : errorMessage(login.error, t("Не удалось войти. Проверьте соединение и попробуйте ещё раз."))
  return <div className="login-page"><main className="login-layout">
    <header className="login-brand"><Brand /><LanguageSwitcher /></header>
    <section className="login-story" aria-label={t("О {0}", { "0": brand.name })}>
      <div className="login-story-copy">
        <h2>{t("Память ваших встреч")}</h2>
        <p className="login-story-tagline">{t("Разговоры остаются с вами.")}</p>
        <p className="login-story-description">{t("Сохраняйте записи встреч, находите нужные слова и задавайте вопросы по стенограмме.")}</p>
      </div>
      <ConversationIllustration />
    </section>
    <section className="login-form-panel" aria-labelledby="login-heading">
    <p className="login-eyebrow">{t("С возвращением")}</p>
    <Title id="login-heading" order={1} size="h2">{t("Войти в рабочее пространство")}</Title>
    <Text c="dimmed" size="sm" className="login-description">{t("Ваши встречи и стенограммы — в одном месте.")}</Text>
    <form className="login-form" noValidate onSubmit={form.handleSubmit((values) => login.mutate(values))}>
      <TextInput label={t("Электронная почта")} id="email" type="email" autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder="name@company.kz" error={localizeMessage(form.formState.errors.email?.message)} {...form.register('email')} />
      <PasswordInput label={t("Пароль")} id="password" autoComplete="current-password" maxLength={1024} error={localizeMessage(form.formState.errors.password?.message)} aria-invalid={Boolean(form.formState.errors.password)}
        visible={passwordVisible} onVisibilityChange={setPasswordVisible} visibilityToggleButtonProps={{ tabIndex: 0, 'aria-label': passwordVisible ? t("Скрыть пароль") : t("Показать пароль") }} {...form.register('password')} />
      {login.isError && <InlineError>{loginError}</InlineError>}
      <Button type="submit" loading={login.isPending} aria-busy={login.isPending} rightSection={<ArrowRight size={18} aria-hidden="true" />}>{login.isPending ? t("Входим…") : t("Войти")}</Button>
    </form>
    </section>
  </main></div>
}
