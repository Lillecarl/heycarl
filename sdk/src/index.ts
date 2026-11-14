// What the shell and every plugin frontend share. Requests carry the gate's
// session cookie, same origin; there is no token in the page.

export class HttpError extends Error {
	constructor(
		readonly status: number,
		readonly body: string
	) {
		super(`HTTP ${status}: ${body}`);
	}
}

let reloading = false;

// The gate answers /api/* with 401 when the session has expired. A full page
// load goes through the gate's sign-in and comes back to the same route.
function signInAgain(): never {
	if (!reloading) {
		reloading = true;
		location.reload();
	}
	throw new HttpError(401, 'signing in again');
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
	const response = await fetch(path, {
		...init,
		headers: { accept: 'application/json', ...init.headers }
	});
	if (response.status === 401) signInAgain();
	if (!response.ok) throw new HttpError(response.status, await response.text());
	if (response.status === 204) return undefined as T;
	const text = await response.text();
	return (text ? JSON.parse(text) : undefined) as T;
}

/** PostgREST, through the kernel: `scope` is `core` or a plugin name. */
export function data<T>(scope: string, path: string, init: RequestInit = {}): Promise<T> {
	return api<T>(`/api/data/${scope}/${path}`, init);
}

export function json(method: string, body: unknown, prefer?: string): RequestInit {
	const headers: Record<string, string> = { 'content-type': 'application/json' };
	if (prefer) headers.prefer = prefer;
	return { method, headers, body: JSON.stringify(body) };
}

export interface PluginInfo {
	name: string;
	title: string;
	frontend: { module: string; pages: Record<string, string> } | null;
}
