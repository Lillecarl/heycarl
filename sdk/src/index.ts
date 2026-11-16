// What the shell and every plugin frontend share. Requests carry the gate's
// session cookie, same origin; there is no token in the page.

import { ApiError, parseError, unreachable } from './errors.ts';

export { ApiError, errorText } from './errors.ts';

let reloading = false;

// The gate answers /api/* with 401 when the session has expired. A full page
// load goes through the gate's sign-in and comes back to the same route.
function signInAgain(): never {
	if (!reloading) {
		reloading = true;
		location.reload();
	}
	throw new ApiError(401, 'Signing in again…');
}

/** Throws `ApiError` for an error answer, and for no answer at all. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
	let response: Response;
	try {
		response = await fetch(path, {
			...init,
			headers: { accept: 'application/json', ...init.headers }
		});
	} catch (e) {
		// fetch rejects with a TypeError when nothing answers; an abort stays an abort.
		throw e instanceof TypeError ? unreachable(e) : e;
	}
	if (response.status === 401) signInAgain();
	if (!response.ok) {
		const text = await response.text().catch(() => '');
		throw parseError(response.status, response.headers.get('content-type'), text);
	}
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

let pluginList: Promise<PluginInfo[]> | null = null;

/** The kernel's plugin list, fetched once per module; a failure is not kept. */
export function plugins(): Promise<PluginInfo[]> {
	pluginList ??= api<PluginInfo[]>('/api/plugins').catch((e: unknown) => {
		pluginList = null;
		throw e;
	});
	return pluginList;
}

/** Each plugin's first page, by plugin name: where its entries open by default. */
export async function pluginPages(): Promise<Record<string, string>> {
	return Object.fromEntries(
		(await plugins()).flatMap((p) => {
			const first = Object.keys(p.frontend?.pages ?? {})[0];
			return first ? [[p.name, `/p/${p.name}/${first}`]] : [];
		})
	);
}
