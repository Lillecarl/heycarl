<!--
	The entries linked to one entry, for any plugin page to embed:
	<Related scope="todos" table="todo" key={{ id: todo.id }} />
	Links live in the core (DESIGN.md "Links"), so this needs no plugin code.
	It renders an h3: the embedding page's entry heading is an h2.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { data, json, pluginPages } from './index.ts';

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

	// null until the first answer, so "Nothing linked" never shows early.
	let links = $state<Link[] | null>(null);
	let query = $state('');
	let found = $state<Entry[]>([]);
	let label = $state('');
	let adding = $state(false);
	let error = $state<string | null>(null);
	let pages = $state<Record<string, string>>({});

	const rpc = <T,>(name: string, args: object) =>
		data<T>('core', `rpc/${name}`, json('POST', args));

	const entryKey = (e: Entry) => JSON.stringify([e.scope, e.table, e.key]);

	function same(a: Entry): boolean {
		return a.scope === scope && a.table === table && JSON.stringify(a.key) === JSON.stringify(key);
	}

	// An entry opens where its plugin says, else on the plugin's first page.
	function href(e: Entry): string {
		return e.url ?? pages[e.scope] ?? '/';
	}

	const name = (e: Entry) => e.label || e.table.replaceAll('_', ' ');

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
		const linked = new Set((links ?? []).map((l) => entryKey(l.entry)));
		found = rows
			.map((r) => r.entry)
			.filter((e) => !same(e) && !linked.has(entryKey(e)))
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

	// load() reads scope, table and key, so the list follows the entry shown.
	$effect(() => {
		run(load);
	});

	onMount(() => {
		run(async () => (pages = await pluginPages()));
		return () => clearTimeout(searchTimer);
	});
</script>

<section class="related">
	<h3>Related</h3>
	{#if error}
		<p class="error" role="alert">{error}</p>
	{/if}
	{#if links === null}
		<p class="muted">Loading…</p>
	{:else if links.length}
		<ul class="plain">
			{#each links as link (link.id)}
				<li>
					<a href={href(link.entry)}>{name(link.entry)}</a>
					<span class="muted"
						>{link.entry.table.replaceAll('_', ' ')}{link.label ? ` · ${link.label}` : ''}</span
					>
					<button class="icon" aria-label="Unlink {name(link.entry)}" onclick={() => remove(link)}
						>×</button
					>
				</li>
			{/each}
		</ul>
	{:else}
		<p class="muted">Nothing linked.</p>
	{/if}
	{#if adding}
		<div class="add">
			<label>
				Find an entry
				<input
					type="search"
					placeholder="A person, todo, reminder…"
					bind:value={query}
					oninput={queueSearch}
					{@attach (el) => el.focus()}
				/>
			</label>
			<label>
				How they relate (optional)
				<input bind:value={label} maxlength="200" />
			</label>
			{#if found.length}
				<ul class="plain found">
					{#each found as e (entryKey(e))}
						<li>
							<button onclick={() => add(e)}>Link {e.label}</button>
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
	h3 {
		margin: 0 0 0.25rem;
	}
	li {
		display: flex;
		gap: 0.5rem;
		align-items: center;
		overflow-wrap: anywhere;
	}
	.add {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		max-width: 28rem;
	}
	.add label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.add > button {
		align-self: flex-start;
	}
	.found button {
		text-align: left;
	}
</style>
