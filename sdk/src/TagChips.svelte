<!--
	An entry's tags as chips, for any plugin page to embed:
	<TagChips scope="todos" table="todo" key={{ id: todo.id }} />
	A tag is a link to an entry of the tags plugin (DESIGN.md "Links").
-->
<script module lang="ts">
	export type Tag = { id: number; name: string; color: string | null };

	/** The chip colour for a tag: its base16 role, or the muted token. */
	export const chipColor = (color: string | null) =>
		color ? `var(--hc-${color})` : 'var(--hc-muted)';
</script>

<script lang="ts">
	import { data, json } from './index.ts';

	type Held = Tag & { link_id: number };

	let {
		scope,
		table,
		key,
		onchange
	}: {
		scope: string;
		table: string;
		key: Record<string, unknown>;
		onchange?: (tags: Tag[]) => void;
	} = $props();

	let held = $state<Held[]>([]);
	let all = $state<Tag[]>([]);
	let adding = $state(false);
	let query = $state('');
	let error = $state<string | null>(null);

	const entry = () => ({ of_scope: scope, of_table: table, of_key: key });
	const rpc = <T,>(name: string, args: object) => data<T>('tags', `rpc/${name}`, json('POST', args));

	let offered = $derived.by(() => {
		const q = query.trim().toLowerCase();
		const mine = new Set(held.map((t) => t.id));
		return all.filter((t) => !mine.has(t.id) && t.name.toLowerCase().includes(q)).slice(0, 8);
	});
	let exact = $derived(all.some((t) => t.name.toLowerCase() === query.trim().toLowerCase()));

	async function run(action: () => Promise<unknown>): Promise<void> {
		try {
			error = null;
			await action();
		} catch (e) {
			error = String(e);
		}
	}

	async function load(): Promise<void> {
		held = await rpc<Held[]>('tags_of', entry());
		onchange?.(held);
	}

	function open(): void {
		adding = true;
		run(async () => {
			all = await data<Tag[]>('tags', 'tag?select=id,name,color&order=name');
		});
	}

	function add(name: string): void {
		const wanted = name.trim();
		if (!wanted) return;
		run(async () => {
			await rpc('tag_entry', { ...entry(), name: wanted });
			query = '';
			adding = false;
			await load();
		});
	}

	function remove(tag: Held): void {
		run(async () => {
			await rpc('untag', { ...entry(), tag_id: tag.id });
			await load();
		});
	}

	// load() reads scope, table and key, so the chips follow the entry shown.
	$effect(() => {
		run(load);
	});
</script>

<div class="tags">
	{#each held as tag (tag.id)}
		<span class="chip" style:--chip={chipColor(tag.color)}>
			{tag.name}
			<button class="icon x" aria-label="Remove tag {tag.name}" onclick={() => remove(tag)}
				>×</button
			>
		</span>
	{/each}
	{#if adding}
		<form
			class="add"
			onsubmit={(e) => {
				e.preventDefault();
				add(query);
			}}
		>
			<input
				aria-label="Tag name"
				placeholder="Tag"
				bind:value={query}
				maxlength="100"
				{@attach (el) => el.focus()}
			/>
			{#each offered as tag (tag.id)}
				<button type="button" class="primary" onclick={() => add(tag.name)}>{tag.name}</button>
			{/each}
			{#if query.trim() && !exact}
				<button type="submit" class="primary">New tag “{query.trim()}”</button>
			{/if}
			<button type="button" onclick={() => (adding = false)}>Cancel</button>
		</form>
	{:else}
		<button onclick={open}>+ Tag</button>
	{/if}
	{#if error}<p class="error" role="alert">{error}</p>{/if}
</div>

<style>
	.tags {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		align-items: center;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		padding-left: 0.75rem;
		border-radius: 999px;
		border: 1px solid var(--chip);
		background: color-mix(in oklab, var(--chip) 18%, transparent);
	}
	.chip .x {
		min-height: 2.5rem;
		min-width: 2.5rem;
	}
	.add {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		width: 100%;
	}
	.add input {
		flex: 1 1 10rem;
	}
	.error {
		margin: 0;
		width: 100%;
	}
</style>
