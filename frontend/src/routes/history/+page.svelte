<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { api, data, json } from '@heycarl/sdk';
	import ErrorNotice from '@heycarl/sdk/ErrorNotice.svelte';

	type Item = { version: number; scope: string; table: string; key: object; label: string };
	type Entry = { entry: number; at: string; via: string | null; items: Item[] };
	type Version = {
		id: number;
		scope: string;
		table: string;
		key: object;
		op: 'insert' | 'update' | 'delete';
		data: Record<string, unknown>;
		at: string;
		via: string | null;
		label: string;
		newest: boolean;
	};
	type Failed = Item & { error: string };
	type Grant = { client_id: string; client_name: string | null };

	const PAGE = 50;
	// The view lives in the URL (?view=changes), so a link opens it.
	let view = $derived(page.url.searchParams.get('view') === 'changes' ? 'changes' : 'trash');
	let trash = $state<Entry[] | null>(null);
	let changes = $state<Version[] | null>(null);
	let more = $state(true);
	let clients = $state<Record<string, string>>({});
	let error = $state<unknown>(null);
	let notice = $state<string | null>(null);
	// The trash entry whose "Delete for good" waits for a second press.
	let purging = $state<number | null>(null);
	let emptying = $state(false);

	// The kernel marks a browser session's writes `app`; an AI connection's
	// `mcp:<client id>`.
	function who(via: string | null): string {
		if (via === null) return 'Hey Carl';
		if (via === 'app') return 'You, in the app';
		if (via.startsWith('mcp:')) {
			const name = clients[via.slice(4)];
			return name ? `AI: ${name}` : 'An AI connection that has ended';
		}
		return via;
	}

	function when(at: string): string {
		return new Date(at).toLocaleString();
	}

	function what(item: { table: string; label: string }): string {
		const table = item.table.replaceAll('_', ' ');
		return item.label ? `${table}: ${item.label}` : table;
	}

	const rpc = <T,>(name: string, args: object = {}) =>
		data<T>('core', `rpc/${name}`, json('POST', args));

	async function attempt(work: () => Promise<void>): Promise<void> {
		error = null;
		try {
			await work();
		} catch (e) {
			error = e;
		}
	}

	async function loadTrash(): Promise<void> {
		trash = await rpc<Entry[]>('trash');
	}

	async function loadChanges(reset: boolean): Promise<void> {
		const last = changes?.at(-1);
		const before = reset || !last ? undefined : last.id;
		const rows = await rpc<Version[]>('history', { max_rows: PAGE, before });
		changes = reset ? rows : [...(changes ?? []), ...rows];
		more = rows.length === PAGE;
	}

	async function loadClients(): Promise<void> {
		const grants = await api<Grant[]>('/api/oauth/grants');
		clients = Object.fromEntries(grants.map((g) => [g.client_id, g.client_name ?? 'an unnamed app']));
	}

	async function act(name: 'restore' | 'undo', version: number): Promise<void> {
		await attempt(async () => {
			const result = await rpc<{ done: number; failed: Failed[]; changed_since: Item[] }>(name, {
				version
			});
			const parts = [result.done === 1 ? '1 record put back.' : `${result.done} records put back.`];
			if (result.failed.length)
				parts.push(
					`These could not come back yet: ${result.failed
						.map((f) => `${what(f)} (${f.error})`)
						.join('; ')}.`
				);
			if (result.changed_since.length)
				parts.push(
					`Left alone, because they changed again since: ${result.changed_since.map(what).join(', ')}.`
				);
			notice = parts.join(' ');
			await Promise.all([loadTrash(), loadChanges(true)]);
		});
	}

	async function purge(entry: Entry): Promise<void> {
		purging = null;
		await attempt(async () => {
			await rpc('purge', { entry: entry.entry });
			notice = 'Deleted for good.';
			await Promise.all([loadTrash(), loadChanges(true)]);
		});
	}

	async function empty(): Promise<void> {
		emptying = false;
		await attempt(async () => {
			await rpc('empty_trash');
			notice = 'The trash is empty.';
			await Promise.all([loadTrash(), loadChanges(true)]);
		});
	}

	onMount(() => {
		attempt(() => Promise.all([loadTrash(), loadChanges(true)]).then());
		// Without the names, a change shows its connection as ended.
		loadClients().catch(() => {});
	});
</script>

<svelte:head><title>History · Hey Carl</title></svelte:head>

<h1>History</h1>
<p class="muted">
	Every change keeps the state before it, and a delete goes to the trash. Only you can empty the
	trash, here: an AI connection can delete, but it cannot make anything gone for good. Text you
	edit out stays in the history until you delete its record from the trash.
</p>

<nav class="views" aria-label="History views">
	<a href="/history" aria-current={view === 'trash' ? 'page' : undefined}
		>Trash{trash?.length ? ` (${trash.length})` : ''}</a
	>
	<a href="/history?view=changes" aria-current={view === 'changes' ? 'page' : undefined}>Changes</a>
</nav>

<ErrorNotice {error} />
{#if notice}
	<p class="notice" role="status">{notice}</p>
{/if}

{#if view === 'trash'}
	<h2 class="visually-hidden">Trash</h2>
	{#if trash === null}
		<p class="muted">Loading…</p>
	{:else if trash.length === 0}
		<p class="muted">The trash is empty.</p>
	{:else}
		<ul class="plain list">
			{#each trash as entry (entry.entry)}
				<li>
					<div>
						<strong>{what(entry.items[0])}</strong>
						{#if entry.items.length > 1}
							<details>
								<summary class="muted">and {entry.items.length - 1} more</summary>
								<ul>
									{#each entry.items.slice(1) as item (item.version)}
										<li>{what(item)}</li>
									{/each}
								</ul>
							</details>
						{/if}
						<div class="muted">Deleted {when(entry.at)} by {who(entry.via)}</div>
					</div>
					<div class="actions">
						<button onclick={() => act('restore', entry.entry)}>Restore</button>
						{#if purging === entry.entry}
							<button class="danger" onclick={() => purge(entry)}
								>Delete {what(entry.items[0])} for good</button
							>
							<button onclick={() => (purging = null)}>Keep</button>
						{:else}
							<button class="danger" onclick={() => (purging = entry.entry)}>Delete for good</button>
						{/if}
					</div>
				</li>
			{/each}
		</ul>
		<div class="actions">
			{#if emptying}
				<button class="danger" onclick={empty}
					>Empty the trash: everything in it, and its history, is gone for good</button
				>
				<button onclick={() => (emptying = false)}>Keep</button>
			{:else}
				<button class="danger" onclick={() => (emptying = true)}>Empty the trash</button>
			{/if}
		</div>
	{/if}
{:else}
	<h2 class="visually-hidden">Changes</h2>
	<p class="muted">
		Undo takes back everything a change did, on every record it touched. Go back to this puts one
		record back to how it was then.
	</p>
	{#if changes === null}
		<p class="muted">Loading…</p>
	{:else if changes.length === 0}
		<p class="muted">No changes yet.</p>
	{:else}
		<ul class="plain list">
			{#each changes as v (v.id)}
				<li>
					<div>
						<span class="op {v.op}"
							>{v.op === 'insert' ? 'added' : v.op === 'update' ? 'changed' : 'deleted'}</span
						>
						<strong>{what(v)}</strong>
						<span class="muted">in {v.scope}</span>
						<div class="muted">{when(v.at)} · {who(v.via)}</div>
					</div>
					<div class="actions">
						<button aria-label="Undo the change to {what(v)}" onclick={() => act('undo', v.id)}
							>Undo</button
						>
						{#if v.op !== 'delete' && !v.newest}
							<button onclick={() => act('restore', v.id)}>Go back to this</button>
						{/if}
					</div>
				</li>
			{/each}
		</ul>
		{#if more}
			<button onclick={() => attempt(() => loadChanges(false))}>Older</button>
		{/if}
	{/if}
{/if}

<style>
	.notice {
		color: var(--hc-accent);
	}
	.views {
		display: flex;
		gap: 0.5rem;
		margin: 1rem 0;
	}
	.views a {
		display: flex;
		align-items: center;
		min-height: 3rem;
		padding: 0 1rem;
		border: 1px solid var(--hc-muted);
		border-radius: 0.5rem;
		color: inherit;
		text-decoration: none;
	}
	.views a[aria-current='page'] {
		border-color: var(--hc-accent);
		color: var(--hc-accent);
		font-weight: bold;
	}
	.list > li {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		align-items: center;
		gap: 0.5rem 1rem;
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--hc-border);
		overflow-wrap: anywhere;
	}
	.list + .actions {
		margin-top: 1rem;
	}
	.op {
		font-size: 0.875rem;
		color: var(--hc-muted);
	}
	.op.delete {
		color: var(--hc-error);
	}
	details ul {
		margin: 0.25rem 0;
	}
</style>
