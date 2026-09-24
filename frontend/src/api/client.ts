import axios, { type AxiosRequestConfig } from 'axios'
import { useAuthStore } from '@/stores/authStore'

const LOCAL_SINGLE_USER = import.meta.env.VITE_AUTH_MODE === 'local_single_user'

export function getApiErrorStatus(error: unknown): number | undefined {
  if (axios.isAxiosError(error)) return error.response?.status
  if (typeof error === 'object' && error !== null && 'status' in error && typeof error.status === 'number') {
    return error.status
  }
}

type ApiClient = {
  get: <T = unknown>(url: string, config?: AxiosRequestConfig) => Promise<T>
  post: <T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig) => Promise<T>
  put: <T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig) => Promise<T>
  patch: <T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig) => Promise<T>
  delete: <T = unknown>(url: string, config?: AxiosRequestConfig) => Promise<T>
}

function createApiClient(baseURL: string): ApiClient {
  const client = axios.create({ baseURL })
  client.interceptors.request.use(config => {
    const token = localStorage.getItem('token')
    if (token) config.headers.Authorization = `Bearer ${token}`
    return config
  })
  client.interceptors.response.use(
    res => res.data != null && typeof res.data === 'object' && res.data.data !== undefined ? res.data.data : res.data,
    (err: unknown) => {
      if (!axios.isAxiosError<unknown>(err)) return Promise.reject(err)
      const status = err.response?.status
      const data = err.response?.data
      const payload = typeof data === 'object' && data !== null ? data : undefined
      const detail = payload && 'detail' in payload ? payload.detail : undefined
      // Public authentication failures belong to the form. A permission
      // denial is not an expired session; this backend uses one specific
      // 403 response when credentials are absent.
      const publicAuthRequest = ['/auth/login', '/auth/register'].includes(err.config?.url ?? '')
      const sessionExpired = status === 401 || (status === 403 && detail === 'Not authenticated')
      if (!LOCAL_SINGLE_USER && !publicAuthRequest && sessionExpired) {
        useAuthStore.getState().logout()
        if (window.location.pathname !== '/login') window.location.href = '/login'
      }
      // Preserve the existing top-level detail contract for callers while
      // retaining the HTTP status for authentication/error-state decisions.
      return Promise.reject(payload ? { ...payload, status } : err)
    }
  )
  return {
    get: <T>(url: string, config?: AxiosRequestConfig) => client.get<T, T>(url, config),
    post: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) => client.post<T, T>(url, data, config),
    put: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) => client.put<T, T>(url, data, config),
    patch: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) => client.patch<T, T>(url, data, config),
    delete: <T>(url: string, config?: AxiosRequestConfig) => client.delete<T, T>(url, config),
  }
}

export const apiClient = createApiClient('/api/v1')
export const apiClientV2 = createApiClient('/api/v2')
