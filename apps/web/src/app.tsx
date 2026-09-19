import { createRootRoute, createRoute, createRouter, lazyRouteComponent, Navigate, Outlet } from '@tanstack/react-router'
import { WorkspaceShell } from './components/shell'
import { meetingSearchSchema, meetingViewSearchSchema } from './lib/contracts'
import { liveSearch } from './lib/live'
import { RouteError, NotFound } from './components/route-errors'

const rootRoute = createRootRoute({ component: Outlet, errorComponent: RouteError, notFoundComponent: NotFound })
const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: '/', component: () => <Navigate to="/meetings" search={{ q: '', offset: 0 }} replace /> })
const loginRoute = createRoute({ getParentRoute: () => rootRoute, path: '/login', component: lazyRouteComponent(() => import('./routes/login'), 'LoginPage') })
const workspaceRoute = createRoute({ getParentRoute: () => rootRoute, id: '_workspace', component: WorkspaceShell })
const meetingsRoute = createRoute({ getParentRoute: () => workspaceRoute, path: '/meetings', validateSearch: (search) => meetingSearchSchema.parse(search), component: lazyRouteComponent(() => import('./routes/meetings'), 'MeetingsPage') })
const newMeetingRoute = createRoute({ getParentRoute: () => workspaceRoute, path: '/meetings/new', component: lazyRouteComponent(() => import('./routes/new-meeting'), 'NewMeetingPage') })
const meetingRoute = createRoute({ getParentRoute: () => workspaceRoute, path: '/meetings/$meetingId', validateSearch: (search) => meetingViewSearchSchema.parse(search), component: lazyRouteComponent(() => import('./routes/meeting-detail'), 'MeetingDetailPage') })
const liveRoute = createRoute({ getParentRoute: () => workspaceRoute, path: '/live', component: lazyRouteComponent(() => import('./routes/live'), 'LivePage') })
const liveRoomRoute = createRoute({ getParentRoute: () => rootRoute, path: '/live/$roomId', validateSearch: (search) => liveSearch.parse(search), component: lazyRouteComponent(() => import('./routes/live-room'), 'LiveRoomPage') })
const chatRoute = createRoute({ getParentRoute: () => workspaceRoute, path: '/chat', component: lazyRouteComponent(() => import('./routes/chat'), 'ChatPage') })
const routeTree = rootRoute.addChildren([indexRoute, loginRoute, liveRoomRoute, workspaceRoute.addChildren([meetingsRoute, newMeetingRoute, meetingRoute, liveRoute, chatRoute])])
export const router = createRouter({ routeTree, scrollRestoration: true })
declare module '@tanstack/react-router' { interface Register { router: typeof router } }
