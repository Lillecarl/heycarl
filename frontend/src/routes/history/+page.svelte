<script lang="ts">
	import { onMount } from 'svelte';

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
	let tab = $state<'trash' | 'changes'>('trash');
	let trash = $state<Entry[] | null>(null);
	let changes = $state<Version[]>([]);
	let more = $state(true);
	let clients = $state<Record<string, string>>({});
	let error = $state<string | null>(null);
	let notice = $state<string | null>(null);

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

	async function rpc<T>(name: string, args: object = {}): Promise<T> {
		const r = await fetch(`/api/data/core/rpc/${name}`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify(args)
		});
		if (!r.ok) throw new Error(`${name}: ${r.status} ${await r.text()}`);
		return r.json();
	}

	async function attempt(work: () => Promise<void>): Promise<void> {
		error = null;
		try {
			await work();
		} catch (e) {
			error = String(e);
		}
	}

	async function loadTrash(): Promise<void> {
		trash = await rpc<Entry[]>('trash');
	}

	async function loadChanges(reset: boolean): Promise<void> {
		const before = reset || changes.length === 0 ? undefined : changes[changes.length - 1].id;
		const page = await rpc<Version[]>('history', { max_rows: PAGE, before });
		changes = reset ? page : [...changes, ...page];
		more = page.length === PAGE;
	}

	async function loadClients(): Promise<void> {
		const r = await fetch('/api/oauth/grants');
		if (!r.ok) return;
		const grants: Grant[] = await r.json();
		clients = Object.fromEntries(
			grants.map((g) => [g.client_id, g.client_name ?? 'an unnamed app'])
		);
	}

	async function restore(version: number): Promise<void> {
		await attempt(async () => {
			const result = await rpc<{ restored: number; updated: boolean; failed: Failed[] }>(
				'restore',
				{ version }
			);
			notice = result.failed.length
				? `Some rows could not come back yet and stay in the trash: ${result.failed
						.map((f) => `${what(f)} (${f.error})`)
						.join('; ')}`
				: 'Restored.';
			await Promise.all([loadTrash(), loadChanges(true)]);
		});
	}

	async function purge(entry: Entry): Promise<void> {
		const names = entry.items.map(what).join(', ');
		if (!confirm(`Delete for good, with all its history? ${names}`)) return;
		await attempt(async () => {
			await rpc('purge', { entry: entry.entry });
			notice = 'Deleted for good.';
			await Promise.all([loadTrash(), loadChanges(true)]);
		});
	}

	async function empty(): Promise<void> {
		if (!confirm('Empty the trash? Everything in it, and its history, is gone for good.')) return;
		await attempt(async () => {
			await rpc('empty_trash');
			notice = 'The trash is empty.';
			await Promise.all([loadTrash(), loadChanges(true)]);
		});
	}

	onMount(() => attempt(() => Promise.all([loadTrash(), loadChanges(true), loadClients()]).then()));
</script>

<h1>History</h1>
<p class="note">
	Every change keeps the state before it, and a delete goes to the trash. Only you can empty the
	trash, here: an AI connection can delete, but it cannot make anything gone for good. Text you
	edit out stays in the history until you delete its record from the trash.
</p>

<div class="tabs" role="tablist">
	<button role="tab" aria-selected={tab === 'trash'} onclick={() => (tab = 'trash')}>
		Trash{trash?.length ? ` (${trash.length})` : ''}
	</button>
	<button role="tab" aria-selected={tab === 'changes'} onclick={() => (tab = 'changes')}>
		Changes
	</button>
</div>

{#if error}
	<p class="error">{error}</p>
{/if}
{#if notice}
	<p class="notice">{notice}</p>
{/if}

{#if tab === 'trash'}
	{#if trash === null}
		<p class="note">Loading…</p>
	{:else if trash.length === 0}
		<p class="note">The trash is empty.</p>
	{:else}
		<ul class="list">
			{#each trash as entry (entry.entry)}
				<li>
					<div>
						<strong>{what(entry.items[0])}</strong>
						{#if entry.items.length > 1}
							<details>
								<summary class="note">and {entry.items.length - 1} more</summary>
								<ul>
									{#each entry.items.slice(1) as item (item.version)}
										<li>{what(item)}</li>
									{/each}
								</ul>
							</details>
						{/if}
						<div class="note">Deleted {when(entry.at)} by {who(entry.via)}</div>
					</div>
					<div class="actions">
						<button onclick={() => restore(entry.entry)}>Restore</button>
						<button class="danger" onclick={() => purge(entry)}>Delete for good</button>
					</div>
				</li>
			{/each}
		</ul>
		<button class="danger" onclick={empty}>Empty the trash</button>
	{/if}
{:else}
	{#if changes.length === 0}
		<p class="note">No changes yet.</p>
	{:else}
		<ul class="list">
			{#each changes as v (v.id)}
				<li>
					<div>
						<span class="op {v.op}">{v.op === 'insert' ? 'added' : v.op === 'update' ? 'changed' : 'deleted'}</span>
						<strong>{what(v)}</strong>
						<span class="note">in {v.scope}</span>
						<div class="note">{when(v.at)} · {who(v.via)}</div>
					</div>
					<div class="actions">
						{#if v.op === 'delete' && v.newest}
							<button onclick={() => restore(v.id)}>Restore</button>
						{:else if v.op !== 'delete' && !v.newest}
							<button onclick={() => restore(v.id)}>Go back to this</button>
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
	.note {
		color: var(--hc-muted);
	}
	.notice {
		color: var(--hc-accent);
	}
	.error {
		color: var(--hc-error);
	}
	.tabs {
		display: flex;
		gap: 0.5rem;
		margin: 1rem 0;
	}
	.tabs [aria-selected='true'] {
		border-color: var(--hc-accent);
		color: var(--hc-accent);
	}
	.list {
		list-style: none;
		padding: 0;
	}
	.list > li {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 1rem;
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--hc-muted);
		overflow-wrap: anywhere;
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		justify-content: flex-end;
	}
	.op {
		font-size: 0.85em;
		color: var(--hc-muted);
	}
	.op.delete {
		color: var(--hc-error);
	}
	details ul {
		margin: 0.25rem 0;
	}
	button {
		font: inherit;
		padding: 0.3rem 0.8rem;
		color: var(--hc-fg);
		background: none;
		border: 1px solid var(--hc-muted);
		border-radius: 6px;
		cursor: pointer;
		white-space: nowrap;
	}
	button.danger {
		border-color: var(--hc-error);
		color: var(--hc-error);
	}
</style>
