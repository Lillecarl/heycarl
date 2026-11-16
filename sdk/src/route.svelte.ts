// A plugin page keeps its view in the hash (#/counter/12), so a link or a
// reminder can open it. Navigate with <a href="#/...">; read with hashId.

class Route {
	hash = $state(location.hash);
}

const route = new Route();
addEventListener('hashchange', () => {
	route.hash = location.hash;
});

/** The id in `#/{name}/{id}`, or null. Reactive: read it in $derived. */
export function hashId(name: string): number | null {
	const prefix = `#/${name}/`;
	if (!route.hash.startsWith(prefix)) return null;
	const rest = route.hash.slice(prefix.length);
	return /^\d+$/.test(rest) ? Number(rest) : null;
}

/** The link to `#/{name}/{id}`, or to the page's start with no arguments. */
export function hashHref(name?: string, id?: number): string {
	return name === undefined ? '#/' : `#/${name}/${id}`;
}

/**
 * Leave a view whose entry is gone. The history entry is replaced, so Back
 * does not open the deleted entry again. Never `location.hash = ''`: an
 * empty hash makes the browser load the page again.
 */
export function leave(to = '#/'): void {
	location.replace(to);
}
