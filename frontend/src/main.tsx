import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'
import './assistant.css'
import './launcher.css'
import './assistant-enhancements.css'
const nativeFetch = window.fetch.bind(window)
window.fetch = (input, init = {}) => {
  const url = typeof input === 'string' ? input : input instanceof Request ? input.url : input.toString()
  const token = localStorage.getItem('moneki_access_token')
  if (token && !url.endsWith('/api/v1/auth/login')) {
    const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined))
    headers.set('Authorization', `Bearer ${token}`)
    init = { ...init, headers }
  }
  return nativeFetch(input, init).then(response => {
    if (response.status === 401 && !url.endsWith('/api/v1/auth/login')) localStorage.removeItem('moneki_access_token')
    return response
  })
}
createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
