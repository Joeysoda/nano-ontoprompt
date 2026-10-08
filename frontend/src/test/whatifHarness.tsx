import { createElement, type ComponentType } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'

export function mount(Component: ComponentType<{ ontologyId: string }>) {
  const app = document.getElementById('root')
  if (app) app.style.display = 'none'
  const host = document.createElement('div')
  host.id = 'repair-test'
  document.body.append(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  createRoot(host).render(
    <QueryClientProvider client={client}>
      <BrowserRouter>{createElement(Component, { ontologyId: 'repair' })}</BrowserRouter>
    </QueryClientProvider>,
  )
}
