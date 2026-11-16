<!--
	The entries linked to one entry, for any plugin page to embed:
	<Related scope="todos" table="todo" key={{ id: todo.id }} />
	Links live in the core (DESIGN.md "Links"), so this needs no plugin code.
-->
<script lang="ts">
	import { api, data, json, type PluginInfo } from './index.ts';

	type Entry = {
		scope: string;
		table: string;
		key: Record<string, unknown>;
		label: string;
		url: string | null;
	};
	type Link = { id: number; label: string; entry: Entry };

	let { scope, table, key }: { scope: string; table: string; key: Record<string, unknown> } =
		$props();

	let links = $state<Link[]>([]);
	let query = $state('');
	let found = $state<Entry[]>([]);
	let label = $state('');
	let adding = $state(false);
	let error = $state<string | null>(null);
	let pages = $state<Record<string, string>>({});

	const rpc = <T,>(name: string, args: object) =>
		data<T>('core', `rpc/${name}`, json('POST', args));

	function same(a: Entry): boolean {
		return a.scope === scope && a.table === table && JSON.stringify(a.key) === JSON.stringify(key);
	}

	// An entry opens where its plugin says, else on the plugin's first page.
	function href(e: Entry): string {
		return e.url ?? pages[e.scope] ?? '/';
	}

	async function run(action: () => Promise<unknown>): Promise<void> {
		try {
			error = null;
			await action();
		} catch (e) {
			error = String(e);
		}
	}

	async function load(): Promise<void> {
		links = await rpc<Link[]>('links', { of_scope: scope, of_table: table, of_key: key });
	}

	// Only the newest search may fill the list: an older answer that arrives
	// late would show results for text no longer in the box.
	let searchGeneration = 0;
	let searchTimer: ReturnType<typeof setTimeout> | undefined;

	function queueSearch(): void {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => run(search), 200);
	}

	async function search(): Promise<void> {
		const mine = ++searchGeneration;
		const q = query.trim();
		if (!q) {
			found = [];
			return;
		}
		const rows = await rpc<{ entry: Entry }[]>('entries', { q, max_rows: 10 });
		if (mine !== searchGeneration) return;
		const linked = new Set(links.map((l) => JSON.stringify([l.entry.scope, l.entry.table, l.entry.key])));
		found = rows
			.map((r) => r.entry)
			.filter((e) => !same(e) && !linked.has(JSON.stringify([e.scope, e.table, e.key])))
			.slice(0, 10);
	}

	function add(e: Entry): void {
		run(async () => {
			await rpc('link', {
				a_scope: scope,
				a_table: table,
				a_key: key,
				b_scope: e.scope,
				b_table: e.table,
				b_key: e.key,
				label: label.trim()
			});
			query = '';
			label = '';
			found = [];
			adding = false;
			await load();
		});
	}

	function remove(link: Link): void {
		run(async () => {
			await rpc('unlink', { id: link.id });
			await load();
		});
	}

	$effect(() => () => clearTimeout(searchTimer));

	$effect(() => {
		// Reload when the page shows another entry.
		void [scope, table, JSON.stringify(key)];
		run(load);
	});

	$effect(() => {
		run(async () => {
			const plugins = await api<PluginInfo[]>('/api/plugins');
			pages = Object.fromEntries(
				plugins
					.filter((p) => p.frontend && Object.keys(p.frontend.pages).length)
					.map((p) => [p.name, `/p/${p.name}/${Object.keys(p.frontend!.pages)[0]}`])
			);
		});
	});
</script>

<section class="related">
	<h4>Related</h4>
	{#if error}
		<p class="error">{error}</p>
	{/if}
	{#if links.length}
		<ul>
			{#each links as link (link.id)}
				<li>
					<a href={href(link.entry)}>{link.entry.label || link.entry.table}</a>
					<span class="muted">{link.entry.table.replaceAll('_', ' ')}{link.label ? ` · ${link.label}` : ''}</span>
					<button class="icon" aria-label="Remove link" onclick={() => remove(link)}>×</button>
				</li>
			{/each}
		</ul>
	{:else}
		<p class="muted">Nothing linked.</p>
	{/if}
	{#if adding}
		<div class="add">
			<input
				placeholder="Find a person, todo, reminder…"
				bind:value={query}
				oninput={queueSearch}
			/>
			<input placeholder="How they relate (optional)" bind:value={label} maxlength="200" />
			{#if found.length}
				<ul class="found">
					{#each found as e (JSON.stringify([e.scope, e.table, e.key]))}
						<li>
							<button onclick={() => add(e)}>{e.label}</button>
							<span class="muted">{e.scope} · {e.table.replaceAll('_', ' ')}</span>
						</li>
					{/each}
				</ul>
			{/if}
			<button onclick={() => (adding = false)}>Cancel</button>
		</div>
	{:else}
		<button onclick={() => (adding = true)}>Link…</button>
	{/if}
</section>

<style>
	.related {
		margin-top: 0.75rem;
	}
	h4 {
		margin: 0 0 0.25rem;
		font-size: 0.95em;
	}
	ul {
		list-style: none;
		padding: 0;
		margin: 0.25rem 0;
	}
	li {
		display: flex;
		gap: 0.5rem;
		align-items: baseline;
		padding: 0.15rem 0;
		overflow-wrap: anywhere;
	}
	a {
		color: var(--hc-accent);
	}
	.muted {
		color: var(--hc-muted);
		font-size: 0.9em;
	}
	.error {
		color: var(--hc-error);
	}
	.add {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		max-width: 28rem;
	}
	input,
	button {
		font: inherit;
		color: var(--hc-fg);
		background: none;
		border: 1px solid var(--hc-muted);
		border-radius: 6px;
		padding: 0.3rem 0.6rem;
	}
	button {
		cursor: pointer;
		align-self: flex-start;
	}
	.found button {
		border: none;
		padding: 0;
		color: var(--hc-accent);
		text-align: left;
	}
	.icon {
		border: none;
		padding: 0 0.3rem;
		color: var(--hc-muted);
	}
</style>
