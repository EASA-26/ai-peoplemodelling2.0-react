/// <reference types="vite/client" />
// Vite's ambient types, above all import.meta.env (and BASE_URL on it, which the router
// basename and the axios base URL are derived from). Scaffolded projects normally have
// this file; this one did not, so tsc rejected import.meta.env as soon as it was used.
