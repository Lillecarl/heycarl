<svelte:options customElement="heycarl-tags" />

<script lang="ts">
	import { onMount } from 'svelte';
	import { api, data, json, type PluginInfo } from '@heycarl/sdk';

	interface Tag {
		id: number;
		name: string;
		color: string | null;
		entries: number;
	}
	interface Entry {
		scope: string;
		table: string;
		key: Record<string, unknown>;
		label: string;
		url: string | null;
	}

	const SCOPE = 'tags';
	const ROLES = [
		['base08', 'Red'],
		['base09', 'Orange'],
		['base0A', 'Yellow'],
		['base0B', 'Green'],
		['base0C', 'Cyan'],
		['base0D', 'Blue'],
		['base0E', 'Purple'],
		['base0F', 'Brown']
	] as const;

	let tags = $state<Tag[]>([]);
	let error = $state<string | null>(null);
	let name = $state('');
	// The view lives in the hash (#/tag/12), so a link to a tag opens it.
	let open = $state<number | null>(parse(location.hash));
	let entries = $state<Entry[]>([]);
	let edit = $state<{ name: string; color: string | null } | null>(null);
	let deleting = $state(false);
	let pages = $state<Record<string, string>>({});

	let current = $derived(tags.find((t) => t.id === open) ?? null);

	function parse(hash: string): number | null {
		const m = /^#\/tag\/(\d+)$/.exec(hash);
		return m ? Number(m[1]) : null;
	}

	const chip = (color: string | null) => (color ? `var(--hc-${color})` : 'var(--hc-muted)');
	const href = (e: Entry) => e.url ?? pages[e.scope] ?? '/';

	async function run(action: () => Promise<unknown>): Promise<void> {
		try {
			error = null;
			await action();
		} catch (e) {
			error = String(e);
		}
	}

	async function load(): Promise<void> {
		tags = await data<Tag[]>(SCOPE, 'tag_summary?order=name');
		if (open !== null && tags.some((t) => t.id === open)) {
			const rows = await data<{ entry: Entry }[]>(
				SCOPE,
				'rpc/tagged',
				json('POST', { tag_id: open })
			);
			entries = rows.map((r) => r.entry);
		} else {
			entries = [];
		}
	}

	function show(id: number | null): void {
		open = id;
		edit = null;
		deleting = false;
		run(load);
	}

	function add(): void {
		const wanted = name.trim();
		if (!wanted) return;
		run(async () => {
			await data(SCOPE, 'tag', json('POST', { name: wanted }));
			name = '';
			await load();
		});
	}

	function save(): void {
		if (!current || !edit) return;
		const { id } = current;
		const changes = { name: edit.name.trim(), color: edit.color };
		run(async () => {
			await data(SCOPE, `tag?id=eq.${id}`, json('PATCH', changes));
			edit = null;
			await load();
		});
	}

	function remove(): void {
		if (!current) return;
		const { id } = current;
		run(async () => {
			await data(SCOPE, `tag?id=eq.${id}`, { method: 'DELETE' });
			location.hash = '#/';
		});
	}

	onMount(() => {
		run(load);
		run(async () => {
			const plugins = await api<PluginInfo[]>('/api/plugins');
			pages = Object.fromEntries(
				plugins
					.filter((p) => p.frontend && Object.keys(p.frontend.pages).length)
					.map((p) => [p.name, `/p/${p.name}/${Object.keys(p.frontend!.pages)[0]}`])
			);
		});
		const onHash = () => show(parse(location.hash));
		addEventListener('hashchange', onHash);
		return () => removeEventListener('hashchange', onHash);
	});
</script>

{#if error}<p class="error" role="alert">{error}</p>{/if}

{#if current}
	<p><a href="#/">← All tags</a></p>
	<h2>
		<span class="dot" style:background={chip(current.color)}></span>
		{current.name}
	</h2>
	{#if edit}
		<form
			class="edit"
			onsubmit={(e) => {
				e.preventDefault();
				save();
			}}
		>
			<input bind:value={edit.name} maxlength="100" required aria-label="Name" />
			<div class="colors" role="radiogroup" aria-label="Colour">
				<button
					type="button"
					class="swatch"
					class:chosen={edit.color === null}
					aria-label="No colour"
					style:--swatch="var(--hc-muted)"
					onclick={() => edit && (edit.color = null)}
				></button>
				{#each ROLES as [role, label] (role)}
					<button
						type="button"
						class="swatch"
						class:chosen={edit.color === role}
						aria-label={label}
						style:--swatch="var(--hc-{role})"
						onclick={() => edit && (edit.color = role)}
					></button>
				{/each}
			</div>
			<div class="actions">
				<button type="submit" class="primary">Save</button>
				<button type="button" onclick={() => (edit = null)}>Cancel</button>
			</div>
		</form>
	{:else}
		<div class="actions">
			<button onclick={() => current && (edit = { name: current.name, color: current.color })}
				>Rename or recolour</button
			>
			{#if deleting}
				<button class="danger" onclick={remove}>Delete for real</button>
				<button onclick={() => (deleting = false)}>Keep</button>
			{:else}
				<button onclick={() => (deleting = true)}>Delete</button>
			{/if}
		</div>
		{#if deleting}
			<p class="muted">
				The {current.entries} tagged entries stay. The tag goes to the trash, and a restore puts
				it back on them.
			</p>
		{/if}
	{/if}
	{#if entries.length}
		<ul class="entries">
			{#each entries as e (JSON.stringify([e.scope, e.table, e.key]))}
				<li>
					<a class="row" href={href(e)}>
						<span class="title">{e.label || e.table}</span>
						<span class="muted">{e.scope} · {e.table.replaceAll('_', ' ')}</span>
					</a>
				</li>
			{/each}
		</ul>
	{:else}
		<p class="muted">Nothing carries this tag.</p>
	{/if}
{:else}
	<form
		class="new"
		onsubmit={(e) => {
			e.preventDefault();
			add();
		}}
	>
		<input placeholder="New tag" bind:value={name} maxlength="100" aria-label="New tag" />
		<button type="submit" class="primary">Add</button>
	</form>
	{#if tags.length}
		<ul class="tags">
			{#each tags as t (t.id)}
				<li>
					<a class="row" href="#/tag/{t.id}">
						<span class="dot" style:background={chip(t.color)}></span>
						<span class="title">{t.name}</span>
						<span class="muted">{t.entries}</span>
					</a>
				</li>
			{/each}
		</ul>
	{:else}
		<p class="muted">No tags yet. Tag anything from its page, or add one here.</p>
	{/if}
{/if}

<style>
	:host {
		display: block;
	}
	button,
	input {
		font: inherit;
		font-size: max(1rem, 16px);
		color: inherit;
		min-height: 3rem;
		padding: 0 1rem;
		border: 1px solid var(--hc-muted, gray);
		border-radius: 0.5rem;
		background: transparent;
		box-sizing: border-box;
		min-width: 0;
	}
	button {
		cursor: pointer;
		touch-action: manipulation;
	}
	.primary {
		border-color: var(--hc-accent, teal);
		color: var(--hc-accent, teal);
	}
	.danger {
		border-color: var(--hc-error, red);
		color: var(--hc-error, red);
	}
	.new,
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin-bottom: 1rem;
	}
	.new input {
		flex: 1 1 12rem;
	}
	.edit {
		display: grid;
		gap: 0.75rem;
		margin-bottom: 1rem;
	}
	.colors {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}
	.swatch {
		width: 3rem;
		padding: 0;
		background: var(--swatch);
		border: 2px solid transparent;
	}
	.swatch.chosen {
		border-color: var(--hc-fg, currentColor);
		outline: 2px solid var(--hc-bg, transparent);
		outline-offset: -4px;
	}
	ul {
		list-style: none;
		padding: 0;
		margin: 0;
	}
	a {
		color: var(--hc-accent, teal);
	}
	a.row {
		display: flex;
		gap: 0.75rem;
		align-items: center;
		min-height: 3rem;
		color: inherit;
		text-decoration: none;
		border-bottom: 1px solid color-mix(in srgb, var(--hc-muted, gray) 30%, transparent);
	}
	.title {
		flex: 1;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	h2 {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}
	.dot {
		display: inline-block;
		width: 0.85rem;
		height: 0.85rem;
		border-radius: 50%;
		flex: none;
	}
	.muted {
		color: var(--hc-muted, gray);
	}
	.error {
		color: var(--hc-error, red);
	}
</style>
