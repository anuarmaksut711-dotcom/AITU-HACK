import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { LocalizedProvider } from './components/localized-provider'
import { setNonce } from 'get-nonce'
import { router } from './app'
import { queryClient } from './lib/query'
import './styles.css'

// The edge supplies a fresh nonce for Mantine variables and scroll-lock styles.
// In Vite development the placeholder remains, and no nonce is required.
const nonce = document.querySelector<HTMLMetaElement>('meta[name="csp-nonce"]')?.content
if (nonce && nonce !== '__AIMEET_CSP_NONCE__') setNonce(nonce)

const root = document.getElementById('root')
if (!root) throw new Error('Application root is missing')
ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <LocalizedProvider nonce={nonce && nonce !== '__AIMEET_CSP_NONCE__' ? nonce : ''}>
      <QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>
    </LocalizedProvider>
  </React.StrictMode>,
)
