<svelte:options customElement={{ tag: 'heycarl-todos', extend: withBase }} />

<script lang="ts">
	import { onMount } from 'svelte';
	import { data, json } from '@heycarl/sdk';
	import { withBase } from '@heycarl/sdk/base';
	import Related from '@heycarl/sdk/Related.svelte';
	import TagChips from '@heycarl/sdk/TagChips.svelte';

	interface Tag {
		id: number;
		name: string;
		color: string | null;
	}
	interface Todo {
		id: number;
		parent_id: number | null;
		list_id: number | null;
		title: string;
		notes: string;
		due: string | null;
		done_at: string | null;
	}
	interface List {
		id: number;
		name: string;
	}

	const FOLDED_KEY = 'heycarl.todos.folded';

	let todos = $state<Todo[]>([]);
	let lists = $state<List[]>([]);
	let current = $state<number | null>(parse(location.hash));
	let title = $state('');
	let error = $state<string | null>(null);
	let open = $state<number | null>(null);
	let adding = $state<number | null>(null);
	let subtitle = $state('');
	let deleting = $state<number | null>(null);
	let folded = $state<Set<number>>(readFolded());
	let naming = $state<'new' | 'rename' | null>(null);
	let listName = $state('');
	let dropping = $state(false);
	let tags = $state<Tag[]>([]);
	// False when this Hey Carl runs no tags plugin.
	let haveTags = $state(false);
	let filter = $state<number | null>(null);
	// The ids carrying the filter tag; null shows every todo.
	let tagged = $state<Set<number> | null>(null);

	let shown = $derived(
		todos.filter((t) => t.list_id === current && (tagged === null || tagged.has(t.id)))
	);
	// Every todo the list holds; a delete takes them all, tag filter or not.
	let inList = $derived(todos.filter((t) => t.list_id === current).length);
	let filterTag = $derived(tags.find((t) => t.id === filter) ?? null);
	let children = $derived(childrenOf(shown));
	let openCount = $derived(countOpen(todos));
	let currentList = $derived(lists.find((l) => l.id === current) ?? null);

	function parse(hash: string): number | null {
		const m = /^#\/list\/(\d+)$/.exec(hash);
		return m ? Number(m[1]) : null;
	}

	function countOpen(list: Todo[]): Map<number | null, number> {
		const out = new Map<number | null, number>();
		for (const t of list) if (!t.done_at) out.set(t.list_id, (out.get(t.list_id) ?? 0) + 1);
		return out;
	}

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
		[todos, lists] = await Promise.all([
			data<Todo[]>('todos', 'todo?order=done_at.nullsfirst,due.nullslast,id'),
			data<List[]>('todos', 'list?select=id,name&order=position,name')
		]);
		await loadTags();
	}

	async function loadTags(): Promise<void> {
		try {
			tags = await data<Tag[]>('tags', 'tag?select=id,name,color&order=name');
			haveTags = true;
		} catch {
			tags = [];
			haveTags = false;
		}
		if (filter !== null && !tags.some((t) => t.id === filter)) filter = null;
		tagged =
			filter === null
				? null
				: new Set(
						(
							await data<{ id: number }[]>(
								'tags',
								'rpc/tagged_keys',
								json('POST', { tag_id: filter, of_scope: 'todos', of_table: 'todo' })
							)
						).map((k) => k.id)
					);
	}

	function filterBy(id: number | null): void {
		filter = filter === id ? null : id;
		run(loadTags);
	}

	const chip = (color: string | null) => (color ? `var(--hc-${color})` : 'var(--hc-muted, gray)');

	function pick(id: number | null): void {
		location.hash = id === null ? '#/' : `#/list/${id}`;
	}

	function startNaming(mode: 'new' | 'rename'): void {
		naming = naming === mode ? null : mode;
		listName = mode === 'rename' ? (currentList?.name ?? '') : '';
		dropping = false;
	}

	function saveList(event: SubmitEvent): void {
		event.preventDefault();
		const name = listName.trim();
		if (!name) return;
		const mode = naming;
		naming = null;
		run(async () => {
			if (mode === 'rename' && current !== null) {
				await data('todos', `list?id=eq.${current}`, json('PATCH', { name }));
				return;
			}
			const [made] = await data<List[]>(
				'todos',
				'list',
				json('POST', { name }, 'return=representation')
			);
			pick(made.id);
		});
	}

	function dropList(): void {
		if (current === null) return;
		if (!dropping) {
			dropping = true;
			return;
		}
		const id = current;
		dropping = false;
		pick(null);
		run(() => data('todos', `list?id=eq.${id}`, { method: 'DELETE' }));
	}

	function moveTo(todo: Todo, value: string): void {
		const list_id = value === '' ? null : Number(value);
		run(() =>
			data('todos', `todo?id=eq.${todo.id}`, json('PATCH', { parent_id: null, list_id }))
		);
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
		const tag = filterTag;
		run(async () => {
			const [made] = await data<Todo[]>(
				'todos',
				'todo',
				json('POST', { title: text, list_id: current }, 'return=representation')
			);
			// Under a tag filter a new todo carries the tag, so it stays in view.
			if (tag)
				await data(
					'tags',
					'rpc/tag_entry',
					json('POST', {
						of_scope: 'todos',
						of_table: 'todo',
						of_key: { id: made.id },
						name: tag.name
					})
				);
		});
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
		const onHash = () => {
			current = parse(location.hash);
			open = null;
			naming = null;
			dropping = false;
		};
		addEventListener('hashchange', onHash);
		return () => removeEventListener('hashchange', onHash);
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
				{#if haveTags}
					<TagChips
						scope="todos"
						table="todo"
						key={{ id: todo.id }}
						onchange={() => loadTags().catch((e) => (error = String(e)))}
					/>
				{/if}
				<Related scope="todos" table="todo" key={{ id: todo.id }} />
				{#if lists.length}
					<label class="move">
						{todo.parent_id === null ? 'Move to' : 'Move out to'}
						<select onchange={(e) => moveTo(todo, e.currentTarget.value)}>
							<option value="" selected={todo.list_id === null && todo.parent_id === null}
								>Inbox</option
							>
							{#each lists as l (l.id)}
								<option value={l.id} selected={todo.list_id === l.id && todo.parent_id === null}
									>{l.name}</option
								>
							{/each}
							{#if todo.parent_id !== null}<option value="-" disabled selected>…</option>{/if}
						</select>
					</label>
				{/if}
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

<nav class="lists" aria-label="Lists">
	<button class="list" aria-current={current === null} onclick={() => pick(null)}>
		Inbox <span class="count">{openCount.get(null) ?? 0}</span>
	</button>
	{#each lists as l (l.id)}
		<button class="list" aria-current={current === l.id} onclick={() => pick(l.id)}>
			{l.name} <span class="count">{openCount.get(l.id) ?? 0}</span>
		</button>
	{/each}
	<button class="list" aria-label="New list" onclick={() => startNaming('new')}>+ List</button>
</nav>

{#if tags.length}
	<nav class="filter" aria-label="Filter by tag">
		{#each tags as t (t.id)}
			<button
				class="tag"
				aria-pressed={filter === t.id}
				style:--chip={chip(t.color)}
				onclick={() => filterBy(t.id)}>{t.name}</button
			>
		{/each}
		{#if filterTag}
			<button class="tag clear" onclick={() => filterBy(null)}>Show all</button>
		{/if}
	</nav>
{/if}

{#if currentList}
	<div class="actions list-actions">
		<button onclick={() => startNaming('rename')}>Rename</button>
		{#if dropping}
			<button class="danger" onclick={dropList}
				>Delete “{currentList.name}” with {inList} todos</button
			>
			<button onclick={() => (dropping = false)}>Keep</button>
		{:else}
			<button class="danger" onclick={dropList}>Delete list</button>
		{/if}
	</div>
{/if}

{#if naming}
	<form class="naming" onsubmit={saveList}>
		<!-- svelte-ignore a11y_autofocus -->
		<input
			bind:value={listName}
			placeholder={naming === 'new' ? 'New list, e.g. Shopping' : 'List name'}
			maxlength="200"
			autofocus
		/>
		<button type="submit">{naming === 'new' ? 'Make' : 'Save'}</button>
	</form>
{/if}

<form onsubmit={add}>
	<input
		bind:value={title}
		placeholder={currentList ? `New todo in ${currentList.name}` : 'New todo'}
		maxlength="500"
	/>
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
	.lists {
		display: flex;
		gap: 0.5rem;
		overflow-x: auto;
		padding-bottom: 0.5rem;
		margin-bottom: 0.5rem;
	}
	.list {
		flex: none;
		display: flex;
		align-items: center;
		gap: 0.5rem;
		white-space: nowrap;
	}
	.list[aria-current='true'] {
		border-color: var(--hc-accent, teal);
		color: var(--hc-accent, teal);
		font-weight: bold;
	}
	.filter {
		display: flex;
		gap: 0.5rem;
		overflow-x: auto;
		padding-bottom: 0.5rem;
		margin-bottom: 0.5rem;
	}
	.tag {
		flex: none;
		white-space: nowrap;
		border-radius: 999px;
		border-color: var(--chip, var(--hc-muted, gray));
	}
	.tag[aria-pressed='true'] {
		background: color-mix(in oklab, var(--chip) 25%, transparent);
		font-weight: bold;
	}
	.tag.clear {
		border-style: dashed;
	}
	.list-actions,
	.naming {
		margin-bottom: 0.5rem;
	}
	.move {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}
	.move select {
		font: inherit;
		font-size: max(1rem, 16px);
		color: inherit;
		min-height: 3rem;
		padding: 0 0.5rem;
		border: 1px solid var(--hc-muted, gray);
		border-radius: 0.5rem;
		background: var(--hc-bg, transparent);
	}
	.error {
		color: var(--hc-error, red);
	}
</style>
