import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs))
}

/**
 * Put an API path under the path the app is served from.
 *
 * axios has a baseURL set for this (src/main.tsx), so anything going through axios
 * needs no help. Use this for the handful of calls that must use fetch() directly --
 * uploads, which need FormData streaming and a readable error body.
 *
 * Why it matters: served at the root, "/api/x" is already right. Served by IIS at
 * /peoplemodelling, a root path leaves this application entirely, and Default Web Site's
 * own rewrite rule sends ^api/(.*) to another team's backend on port 9000. A bare
 * fetch("/api/uploads/chunk/...") therefore uploaded into somebody else's app, and the
 * only symptom was "Chunk upload failed".
 *
 * BASE_URL is "/" or "/peoplemodelling/", always with a trailing slash.
 */
export function apiUrl(path: string): string {
    const base = import.meta.env.BASE_URL.replace(/\/+$/, "")
    return `${base}${path.startsWith("/") ? path : `/${path}`}`
}
