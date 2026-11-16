// base16 themes. The list is static/themes/themes.json (nix/themes.nix).
// The choice lives in the person's preference row; this browser keeps a
// copy under STORAGE_KEY, which app.html reads before the first paint.

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
const PREFERENCE = '/api/data/core/preference';
const SET_THEME = '/api/data/core/rpc/preference_set_theme';

/** The slug saved for this person, or null for the system default. */
export async function savedTheme(): Promise<string | null> {
	const response = await fetch(PREFERENCE);
	if (!response.ok) throw new Error(`The saved theme did not load: ${response.status}`);
	const rows: { theme: string | null }[] = await response.json();
	return rows[0]?.theme ?? null;
}

export async function saveTheme(slug: string | null): Promise<void> {
	const response = await fetch(SET_THEME, {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({ theme: slug })
	});
	if (!response.ok) throw new Error(`The theme was not saved: ${response.status}`);
}

export async function loadThemes(): Promise<Theme[]> {
	const response = await fetch('/themes/themes.json');
	if (!response.ok) throw new Error(`The themes did not load: ${response.status}`);
	return response.json();
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
