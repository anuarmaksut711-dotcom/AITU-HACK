import { t, useLocale } from '../i18n'
import { Link, Navigate, Outlet, useNavigate, useRouterState } from '@tanstack/react-router'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { useMediaQuery } from '@mantine/hooks'
import { CalendarDays, LogOut, MessageSquare, Radio, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { ActionIcon, Anchor, Avatar, NavLink } from '@mantine/core'
import { api, errorMessage } from '../lib/api'
import { queryClient, sessionQuery } from '../lib/query'
import { Brand, ErrorState, InlineError, LoadingState } from './ui'
import { LanguageSwitcher } from './language-switcher'
import { brand } from '../brand'

export function WorkspaceShell() {
  useLocale()
  const session = useQuery(sessionQuery)
  if (session.isPending) return <main className="main-content"><LoadingState /></main>
  if (session.isError) return <main className="main-content"><ErrorState description={errorMessage(session.error)} onRetry={() => void session.refetch()} /></main>
  if (!session.data) return <Navigate to="/login" replace />
  return <WorkspaceFrame><Outlet /></WorkspaceFrame>
}

export function WorkspaceFrame({ children }: { children: ReactNode }) {
  useLocale()
  const navigate = useNavigate()
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const session = useQuery(sessionQuery)
  const mobile = useMediaQuery('(max-width: 760px)')
  const [mobileExpanded, setMobileExpanded] = useState(false)
  const [desktopCollapsed, setDesktopCollapsed] = useState(() => {
    try { return localStorage.getItem('soyle-sidebar-collapsed') === '1' } catch { return false }
  })
  const collapsed = mobile ? !mobileExpanded : desktopCollapsed
  function toggleSidebar() {
    if (mobile) setMobileExpanded((value) => !value)
    else {
      setDesktopCollapsed(!desktopCollapsed)
      try { localStorage.setItem('soyle-sidebar-collapsed', desktopCollapsed ? '0' : '1') } catch { /* Storage may be unavailable. */ }
    }
  }
  const logout = useMutation({
    mutationFn: api.logout,
    onSuccess: () => {
      queryClient.clear()
      queryClient.setQueryData(['session'], null)
      void navigate({ to: '/login', replace: true })
    },
  })
  return <div className={`app-shell workspace-frame ${collapsed ? 'sidebar-collapsed' : ''} ${mobileExpanded ? 'sidebar-mobile-expanded' : ''} ${/^\/live\/[^/]+/.test(pathname) ? 'workspace-live-room' : ''} ${/^\/live\/?$/.test(pathname) ? 'workspace-live-entry' : ''} ${pathname.startsWith('/meetings') ? 'workspace-meetings' : ''} ${pathname === '/chat' ? 'workspace-chat-route' : ''}`}>
    <Anchor className="skip-link" href="#main-content">{t("К содержимому")}</Anchor>
    {mobile && mobileExpanded && <button type="button" className="sidebar-backdrop" aria-label={t("Закрыть навигацию")} onClick={() => setMobileExpanded(false)} />}
    <aside className="sidebar">
      <div className="sidebar-header">
        <Anchor className="sidebar-brand" underline="never" renderRoot={(props) => <Link {...props} to="/meetings" search={{ q: '', offset: 0 }} aria-label={t("{0} — все встречи", { "0": brand.name })} />}><Brand /></Anchor>
        <ActionIcon className="sidebar-toggle" variant="subtle" color="forest" size="lg" aria-label={collapsed ? t("Развернуть боковую панель") : t("Свернуть боковую панель")} title={collapsed ? t("Развернуть боковую панель") : t("Свернуть боковую панель")} aria-expanded={!collapsed} aria-controls="workspace-navigation" onClick={toggleSidebar}>
          {collapsed ? <PanelLeftOpen size={20} /> : <PanelLeftClose size={20} />}
        </ActionIcon>
      </div>
      <nav id="workspace-navigation" className="sidebar-nav" onClick={() => setMobileExpanded(false)} aria-label={t("Основная навигация")}>
        <NavLink active={pathname.startsWith('/meetings')} label={t("Встречи")} aria-label={t("Встречи")} title={collapsed ? t("Встречи") : undefined} leftSection={<CalendarDays size={21} aria-hidden="true" />} className="nav-link"
          renderRoot={(props) => <Link {...props} to="/meetings" search={{ q: '', offset: 0 }} activeOptions={{ includeSearch: false }} />} />
        <NavLink active={pathname.startsWith('/chat')} label={t("Чат")} aria-label={t("Чат")} title={collapsed ? t("Чат") : undefined} leftSection={<MessageSquare size={21} aria-hidden="true" />} className="nav-link"
          renderRoot={(props) => <Link {...props} to="/chat" />} />
      <NavLink active={pathname.startsWith('/live')} label="Live" aria-label="Live" title={collapsed ? 'Live' : undefined} leftSection={<Radio size={21} aria-hidden="true" />} className="nav-link" renderRoot={(props) => <Link {...props} to="/live" />} />
      </nav>
      {session.data && <div className="sidebar-footer">
        <div className="account" title={session.data.email}>
          <Avatar size="sm" radius="xl" color="gray" aria-hidden="true">{(session.data.display_name || session.data.email).slice(0, 1).toUpperCase()}</Avatar>
          <span className="account-info"><strong>{session.data.display_name || t("Рабочая учётная запись")}</strong><span>{session.data.email}</span></span>
          <ActionIcon variant="subtle" color="gray" className="account-signout" size="lg" aria-label={t("Выйти")} title={t("Выйти")} onClick={() => logout.mutate()} loading={logout.isPending} aria-busy={logout.isPending}>
            <LogOut size={20} aria-hidden="true" />
          </ActionIcon>
        </div>
        {logout.isError && <InlineError>{errorMessage(logout.error, t("Не удалось выйти. Попробуйте ещё раз."))}</InlineError>}
        <LanguageSwitcher compact={collapsed} />
      </div>}
    </aside>
    <main id="main-content" className="main-content" tabIndex={-1}>{children}</main>
  </div>
}
