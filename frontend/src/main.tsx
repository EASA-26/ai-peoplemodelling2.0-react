import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import axios from 'axios'
// @ts-ignore
import './index.css'
import App from './App.tsx'

// Send every API call under the path the app is served from. The pages ask for
// relative paths like "/api/analytics/summary"; served at the root those are already
// right, but under IIS at /peoplemodelling they would leave the application and hit
// the shared site -- where Default Web Site's own rewrite rule sends ^api/(.*) to
// another team's backend on port 9000. BASE_URL is the build's base (vite.config.ts),
// so this resolves to "" at the root and "/peoplemodelling" under IIS.
//
// Empty is the same as unset to axios, so the root build is unchanged by this.
axios.defaults.baseURL = import.meta.env.BASE_URL.replace(/\/+$/, '')

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
