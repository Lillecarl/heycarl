<!--
	An entry's tags as chips, for any plugin page to embed:
	<TagChips scope="todos" table="todo" key={{ id: todo.id }} />
	A tag is a link to an entry of the tags plugin (DESIGN.md "Links").
-->
<script lang="ts">
	import { data, json } from './index.ts';

	export type Tag = { id: number; name: string; color: string | null };
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
	let exact = $derived(
		all.some((t) => t.name.toLowerCase() === query.trim().toLowerCase())
	);

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

	$effect(() => {
		void [scope, table, JSON.stringify(key)];
		run(load);
	});
</script>

<div class="tags">
	{#each held as tag (tag.id)}
		<span class="chip" style:--chip={tag.color ? `var(--hc-${tag.color})` : 'var(--hc-muted)'}>
			{tag.name}
			<button class="x" aria-label="Remove tag {tag.name}" onclick={() => remove(tag)}>×</button>
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
			<!-- svelte-ignore a11y_autofocus -->
			<input placeholder="Tag" bind:value={query} maxlength="100" autofocus />
			{#each offered as tag (tag.id)}
				<button type="button" class="pick" onclick={() => add(tag.name)}>{tag.name}</button>
			{/each}
			{#if query.trim() && !exact}
				<button type="submit" class="pick">New tag “{query.trim()}”</button>
			{/if}
			<button type="button" onclick={() => (adding = false)}>Cancel</button>
		</form>
	{:else}
		<button class="more" onclick={open}>+ Tag</button>
	{/if}
	{#if error}<p class="error">{error}</p>{/if}
</div>

<style>
	.tags {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
		align-items: center;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 0.15rem;
		min-height: 2.25rem;
		padding-left: 0.7rem;
		border-radius: 999px;
		border: 1px solid var(--chip);
		background: color-mix(in oklab, var(--chip) 18%, transparent);
		color: var(--hc-fg);
	}
	button,
	input {
		font: inherit;
		font-size: max(1rem, 16px);
		color: var(--hc-fg);
		background: none;
		border: 1px solid var(--hc-muted);
		border-radius: 999px;
		min-height: 3rem;
		padding: 0 0.9rem;
		cursor: pointer;
	}
	.x {
		border: none;
		min-width: 2.25rem;
		min-height: 2.25rem;
		padding: 0;
		color: var(--hc-muted);
	}
	.add {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
		width: 100%;
	}
	input {
		flex: 1 1 10rem;
		border-radius: 6px;
		cursor: text;
	}
	.pick {
		border-color: var(--hc-accent);
		color: var(--hc-accent);
	}
	.error {
		color: var(--hc-error);
		margin: 0;
		width: 100%;
	}
</style>
