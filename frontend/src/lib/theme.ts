// base16 themes. The list is static/themes/themes.json (nix/themes.nix).
// The choice lives in the person's preference row; this browser keeps a
// copy under STORAGE_KEY, which app.html reads before the first paint.

import { api, data, json } from '@heycarl/sdk';

export const ROLES = [
	'base00', 'base01', 'base02', 'base03', 'base04', 'base05', 'base06', 'base07',
	'base08', 'base09', 'base0A', 'base0B', 'base0C', 'base0D', 'base0E', 'base0F'
] as const;

export type Role = (typeof ROLES)[number];

export type Theme = {
	slug: string;
	name: string;
	author: string;
	variant: 'light' | 'dark';
	palette: Record<Role, string>;
};

const STORAGE_KEY = 'heycarl.theme';

/** The slug saved for this person, or null for the system default. */
export async function savedTheme(): Promise<string | null> {
	const rows = await data<{ theme: string | null }[]>('core', 'preference');
	return rows[0]?.theme ?? null;
}

export async function saveTheme(slug: string | null): Promise<void> {
	await data('core', 'rpc/preference_set_theme', json('POST', { theme: slug }));
}

export function loadThemes(): Promise<Theme[]> {
	return api<Theme[]>('/themes/themes.json');
}

export function storedTheme(): string | null {
	try {
		return (JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null') as Theme | null)?.slug ?? null;
	} catch {
		return null;
	}
}

/** Applies a theme and remembers it, or with null goes back to the defaults. */
export function useTheme(theme: Theme | null): void {
	const root = document.documentElement.style;
	for (const role of ROLES) {
		if (theme) root.setProperty(`--hc-${role}`, theme.palette[role]);
		else root.removeProperty(`--hc-${role}`);
	}
	root.colorScheme = theme?.variant ?? '';
	try {
		if (theme) localStorage.setItem(STORAGE_KEY, JSON.stringify(theme));
		else localStorage.removeItem(STORAGE_KEY);
	} catch {
		// No storage: the theme holds until the page reloads.
	}
}
