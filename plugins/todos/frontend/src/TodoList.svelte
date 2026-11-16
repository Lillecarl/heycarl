<svelte:options customElement="heycarl-todos" />

<script lang="ts">
	import { onMount } from 'svelte';
	import { data, json } from '@heycarl/sdk';
	import Related from '@heycarl/sdk/Related.svelte';

	interface Todo {
		id: number;
		parent_id: number | null;
		title: string;
		notes: string;
		due: string | null;
		done_at: string | null;
	}

	const FOLDED_KEY = 'heycarl.todos.folded';

	let todos = $state<Todo[]>([]);
	let title = $state('');
	let error = $state<string | null>(null);
	let open = $state<number | null>(null);
	let adding = $state<number | null>(null);
	let subtitle = $state('');
	let deleting = $state<number | null>(null);
	let folded = $state<Set<number>>(readFolded());

	let children = $derived(childrenOf(todos));

	function readFolded(): Set<number> {
		try {
			const ids: unknown = JSON.parse(localStorage.getItem(FOLDED_KEY) ?? '[]');
			return new Set(Array.isArray(ids) ? ids.filter((i) => typeof i === 'number') : []);
		} catch {
			return new Set();
		}
	}

	function fold(id: number): void {
		const next = new Set(folded);
		if (!next.delete(id)) next.add(id);
		folded = next;
		try {
			localStorage.setItem(FOLDED_KEY, JSON.stringify([...next]));
		} catch {
			// No storage: the fold holds until the page reloads.
		}
	}

	// A todo whose parent is missing shows at the top level.
	function childrenOf(list: Todo[]): Map<number | null, Todo[]> {
		const ids = new Set(list.map((t) => t.id));
		const out = new Map<number | null, Todo[]>();
		for (const t of list) {
			const parent = t.parent_id !== null && ids.has(t.parent_id) ? t.parent_id : null;
			out.set(parent, [...(out.get(parent) ?? []), t]);
		}
		return out;
	}

	function count(id: number): number {
		return (children.get(id) ?? []).reduce((n, c) => n + 1 + count(c.id), 0);
	}

	async function load(): Promise<void> {
		todos = await data<Todo[]>('todos', 'todo?order=done_at.nullsfirst,due.nullslast,id');
	}

	async function run(action: () => Promise<unknown>): Promise<void> {
		try {
			error = null;
			await action();
			await load();
		} catch (e) {
			error = String(e);
		}
	}

	function add(event: SubmitEvent): void {
		event.preventDefault();
		const text = title.trim();
		if (!text) return;
		title = '';
		run(() => data('todos', 'todo', json('POST', { title: text })));
	}

	function addSub(event: SubmitEvent, parent: Todo): void {
		event.preventDefault();
		const text = subtitle.trim();
		if (!text) return;
		subtitle = '';
		adding = null;
		if (folded.has(parent.id)) fold(parent.id);
		run(() => data('todos', 'todo', json('POST', { title: text, parent_id: parent.id })));
	}

	function startSub(todo: Todo): void {
		adding = adding === todo.id ? null : todo.id;
		subtitle = '';
	}

	function toggle(todo: Todo): void {
		const done_at = todo.done_at ? null : new Date().toISOString();
		run(() => data('todos', `todo?id=eq.${todo.id}`, json('PATCH', { done_at })));
	}

	function saveNotes(todo: Todo, notes: string): void {
		if (notes === todo.notes) return;
		run(() => data('todos', `todo?id=eq.${todo.id}`, json('PATCH', { notes })));
	}

	function remove(todo: Todo): void {
		if (count(todo.id) > 0 && deleting !== todo.id) {
			deleting = todo.id;
			return;
		}
		deleting = null;
		run(() => data('todos', `todo?id=eq.${todo.id}`, { method: 'DELETE' }));
	}

	onMount(() => {
		load().catch((e) => (error = String(e)));
	});
</script>

{#snippet node(todo: Todo)}
	{@const kids = children.get(todo.id) ?? []}
	{@const shut = folded.has(todo.id)}
	<li>
		<div class="row" class:done={todo.done_at}>
			{#if kids.length}
				<button
					class="icon fold"
					aria-label={shut ? 'Show subtasks' : 'Hide subtasks'}
					aria-expanded={!shut}
					onclick={() => fold(todo.id)}>{shut ? '▸' : '▾'}</button
				>
			{:else}
				<span class="icon"></span>
			{/if}
			<input
				type="checkbox"
				aria-label="Done"
				checked={!!todo.done_at}
				onchange={() => toggle(todo)}
			/>
			<button
				class="title"
				aria-expanded={open === todo.id}
				onclick={() => (open = open === todo.id ? null : todo.id)}
			>
				<span class="text">{todo.title}</span>
				{#if shut}<span class="count">{count(todo.id)}</span>{/if}
				{#if todo.notes}<span class="count" aria-label="Has notes">✎</span>{/if}
			</button>
			<button class="icon" aria-label="Add subtask" onclick={() => startSub(todo)}>+</button>
		</div>
		{#if open === todo.id}
			<div class="detail">
				<textarea
					placeholder="Notes"
					value={todo.notes}
					onblur={(e) => saveNotes(todo, e.currentTarget.value)}
				></textarea>
				<Related scope="todos" table="todo" key={{ id: todo.id }} />
				<div class="actions">
					{#if deleting === todo.id}
						<button class="danger" onclick={() => remove(todo)}
							>Delete with {count(todo.id)} subtasks</button
						>
						<button onclick={() => (deleting = null)}>Keep</button>
					{:else}
						<button class="danger" onclick={() => remove(todo)}>Delete</button>
					{/if}
				</div>
			</div>
		{/if}
		{#if adding === todo.id}
			<form class="sub" onsubmit={(e) => addSub(e, todo)}>
				<!-- svelte-ignore a11y_autofocus -->
				<input bind:value={subtitle} placeholder="New subtask" maxlength="500" autofocus />
				<button type="submit">Add</button>
			</form>
		{/if}
		{#if kids.length && !shut}
			<ul>
				{#each kids as kid (kid.id)}
					{@render node(kid)}
				{/each}
			</ul>
		{/if}
	</li>
{/snippet}

<form onsubmit={add}>
	<input bind:value={title} placeholder="New todo" maxlength="500" />
	<button type="submit">Add</button>
</form>

{#if error}<p class="error" role="alert">{error}</p>{/if}

<ul class="tree">
	{#each children.get(null) ?? [] as todo (todo.id)}
		{@render node(todo)}
	{/each}
</ul>

<style>
	:host {
		display: block;
	}
	button,
	input,
	textarea {
		font: inherit;
		font-size: max(1rem, 16px);
		color: inherit;
	}
	button {
		min-height: 3rem;
		padding: 0 1rem;
		border: 1px solid var(--hc-muted, gray);
		border-radius: 0.5rem;
		background: transparent;
		cursor: pointer;
	}
	form {
		display: flex;
		gap: 0.5rem;
	}
	form input {
		flex: 1;
		min-width: 0;
		min-height: 3rem;
		padding: 0 0.75rem;
		border: 1px solid var(--hc-muted, gray);
		border-radius: 0.5rem;
		background: transparent;
	}
	ul {
		list-style: none;
		padding: 0;
		margin: 0;
	}
	ul ul {
		padding-left: 1.25rem;
		border-left: 1px solid color-mix(in srgb, var(--hc-muted, gray) 30%, transparent);
		margin-left: 1.5rem;
	}
	.tree {
		margin-top: 1rem;
	}
	.row {
		display: grid;
		grid-template-columns: 3rem 2rem 1fr 3rem;
		align-items: center;
		min-height: 3rem;
		border-bottom: 1px solid color-mix(in srgb, var(--hc-muted, gray) 30%, transparent);
	}
	.row input[type='checkbox'] {
		width: 1.25rem;
		height: 1.25rem;
		margin: 0;
	}
	.icon {
		width: 3rem;
		padding: 0;
		border: none;
		font-size: 1.25rem;
		color: var(--hc-muted, gray);
	}
	.title {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		min-width: 0;
		padding: 0 0.25rem;
		border: none;
		text-align: left;
	}
	.title .text {
		flex: 1;
		overflow-wrap: anywhere;
	}
	.title[aria-expanded='true'] .text {
		font-weight: bold;
	}
	.count {
		color: var(--hc-muted, gray);
		font-size: 0.875rem;
		font-variant-numeric: tabular-nums;
	}
	.done .text {
		opacity: 0.5;
		text-decoration: line-through;
	}
	.detail {
		display: grid;
		gap: 0.5rem;
		padding: 0.5rem 0 1rem 5rem;
	}
	.detail textarea {
		min-height: 5rem;
		padding: 0.5rem;
		border: 1px solid var(--hc-muted, gray);
		border-radius: 0.5rem;
		background: transparent;
		resize: vertical;
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}
	.danger {
		border-color: var(--hc-error, red);
		color: var(--hc-error, red);
	}
	.sub {
		padding: 0.5rem 0 0.5rem 5rem;
	}
	.error {
		color: var(--hc-error, red);
	}
</style>
