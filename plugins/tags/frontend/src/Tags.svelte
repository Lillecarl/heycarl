<svelte:options customElement={{ tag: 'heycarl-tags', extend: withBase }} />

<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { data, json, pluginPages } from '@heycarl/sdk';
	import { withBase } from '@heycarl/sdk/base';
	import { hashHref, hashId, leave } from '@heycarl/sdk/route';
	import { chipColor } from '@heycarl/sdk/TagChips.svelte';

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
		['', 'None'],
		['base08', 'Red'],
		['base09', 'Orange'],
		['base0A', 'Yellow'],
		['base0B', 'Green'],
		['base0C', 'Cyan'],
		['base0D', 'Blue'],
		['base0E', 'Purple'],
		['base0F', 'Brown']
	] as const;

	// null until the first answer.
	let tags = $state<Tag[] | null>(null);
	let error = $state<string | null>(null);
	let name = $state('');
	let open = $derived(hashId('tag'));
	let entries = $state<Entry[] | null>(null);
	// color '' is no colour: a radio group needs a string value.
	let edit = $state<{ name: string; color: string } | null>(null);
	let deleting = $state(false);
	let pages = $state<Record<string, string>>({});

	let current = $derived(tags?.find((t) => t.id === open) ?? null);

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
		await loadEntries(open);
	}

	async function loadEntries(id: number | null): Promise<void> {
		if (id === null) return;
		const rows = await data<{ entry: Entry }[]>(SCOPE, 'rpc/tagged', json('POST', { tag_id: id }));
		if (open === id) entries = rows.map((r) => r.entry);
	}

	function add(event: SubmitEvent): void {
		event.preventDefault();
		const wanted = name.trim();
		if (!wanted) return;
		run(async () => {
			await data(SCOPE, 'tag', json('POST', { name: wanted }));
			name = '';
			await load();
		});
	}

	function save(event: SubmitEvent): void {
		event.preventDefault();
		if (!current || !edit) return;
		const { id } = current;
		const changes = { name: edit.name.trim(), color: edit.color || null };
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
			leave();
			await load();
		});
	}

	// The hash names the open tag; its entries load when it changes.
	$effect(() => {
		const id = open;
		untrack(() => {
			edit = null;
			deleting = false;
			entries = null;
			run(() => loadEntries(id));
		});
	});

	onMount(() => {
		run(load);
		run(async () => (pages = await pluginPages()));
	});
</script>

{#if error}<p class="error" role="alert">{error}</p>{/if}

{#if open !== null && current}
	<p><a href={hashHref()}>← All tags</a></p>
	<h2>
		<span class="dot" style:background={chipColor(current.color)}></span>
		{current.name}
	</h2>
	{#if edit}
		<form class="edit" onsubmit={save}>
			<label>Name <input bind:value={edit.name} maxlength="100" required /></label>
			<fieldset>
				<legend>Colour</legend>
				{#each ROLES as [role, label] (role)}
					<label class="swatch" style:--swatch={chipColor(role || null)}>
						<input type="radio" name="colour" value={role} bind:group={edit.color} />
						<span class="visually-hidden">{label}</span>
					</label>
				{/each}
			</fieldset>
			<div class="actions">
				<button type="submit" class="primary">Save</button>
				<button type="button" onclick={() => (edit = null)}>Cancel</button>
			</div>
		</form>
	{:else}
		<div class="actions">
			<button onclick={() => current && (edit = { name: current.name, color: current.color ?? '' })}
				>Rename or recolour</button
			>
			{#if deleting}
				<button class="danger" onclick={remove}>Delete “{current.name}”</button>
				<button onclick={() => (deleting = false)}>Keep</button>
			{:else}
				<button onclick={() => (deleting = true)}>Delete</button>
			{/if}
		</div>
		{#if deleting}
			<p class="muted">
				The {current.entries} tagged entries stay. The tag goes to the trash, and a restore puts it
				back on them.
			</p>
		{/if}
	{/if}
	{#if entries === null}
		<p class="muted">Loading…</p>
	{:else if entries.length}
		<ul class="plain">
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
{:else if open !== null && tags !== null}
	<p><a href={hashHref()}>← All tags</a></p>
	<p>No such tag.</p>
{:else}
	<form class="actions new" onsubmit={add}>
		<input placeholder="New tag" bind:value={name} maxlength="100" aria-label="New tag" />
		<button type="submit" class="primary">Add</button>
	</form>
	{#if tags === null}
		<p class="muted">Loading…</p>
	{:else if tags.length}
		<ul class="plain">
			{#each tags as t (t.id)}
				<li>
					<a class="row" href={hashHref('tag', t.id)}>
						<span class="dot" style:background={chipColor(t.color)}></span>
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
	.new,
	.actions {
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
	.edit > label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	fieldset {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin: 0;
		padding: 0;
		border: none;
	}
	legend {
		padding: 0;
		margin-bottom: 0.25rem;
	}
	.swatch {
		position: relative;
		display: grid;
		place-items: center;
		width: 3rem;
		height: 3rem;
		border-radius: 0.5rem;
		background: var(--swatch);
		border: 2px solid transparent;
		cursor: pointer;
	}
	.swatch input {
		opacity: 0;
		position: absolute;
	}
	.swatch:has(input:checked) {
		border-color: var(--hc-fg);
		outline: 2px solid var(--hc-bg);
		outline-offset: -4px;
	}
	.swatch:has(input:focus-visible) {
		outline: 2px solid var(--hc-accent);
		outline-offset: 2px;
	}
	a.row {
		display: flex;
		gap: 0.75rem;
		align-items: center;
		min-height: 3rem;
		color: inherit;
		text-decoration: none;
		border-bottom: 1px solid var(--hc-border);
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
</style>
