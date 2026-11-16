<svelte:options customElement={{ tag: 'heycarl-todos', extend: withBase }} />

<script lang="ts">
	import { onMount } from 'svelte';
	import { data, json } from '@heycarl/sdk';
	import { withBase } from '@heycarl/sdk/base';
	import { hashHref, hashId, leave } from '@heycarl/sdk/route';
	import Related from '@heycarl/sdk/Related.svelte';
	import TagChips, { chipColor, type Tag } from '@heycarl/sdk/TagChips.svelte';

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

	// null until the first answer.
	let todos = $state<Todo[] | null>(null);
	let lists = $state<List[]>([]);
	// The open list lives in the hash (#/list/3); no list is the Inbox.
	let current = $derived(hashId('list'));
	let title = $state('');
	let error = $state<string | null>(null);
	let open = $state<number | null>(null);
	let adding = $state<number | null>(null);
	let subtitle = $state('');
	let deleting = $state<number | null>(null);
	let folded = $state<Set<number>>(readFolded());
	// The naming form and the armed delete belong to the list they opened on:
	// on another list they are closed, so a confirm never carries over.
	let namingOn = $state<{ list: number | null; mode: 'new' | 'rename' } | null>(null);
	let naming = $derived(namingOn?.list === current ? namingOn.mode : null);
	let listName = $state('');
	let droppingOn = $state<number | null>(null);
	let dropping = $derived(current !== null && droppingOn === current);
	let tags = $state<Tag[]>([]);
	// False when this Hey Carl runs no tags plugin.
	let haveTags = $state(false);
	let filter = $state<number | null>(null);
	// The ids carrying the filter tag; null shows every todo.
	let tagged = $state<Set<number> | null>(null);

	let all = $derived(todos ?? []);
	let shown = $derived(
		all.filter((t) => t.list_id === current && (tagged === null || tagged.has(t.id)))
	);
	// Every todo the list holds; a delete takes them all, tag filter or not.
	let inList = $derived(all.filter((t) => t.list_id === current).length);
	let filterTag = $derived(tags.find((t) => t.id === filter) ?? null);
	let children = $derived(childrenOf(shown));
	let openCount = $derived(countOpen(all));
	let currentList = $derived(lists.find((l) => l.id === current) ?? null);
	// A link to a deleted list: nothing to add to, and nothing to show.
	let missing = $derived(todos !== null && current !== null && !currentList);

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

	function startNaming(mode: 'new' | 'rename'): void {
		namingOn = naming === mode ? null : { list: current, mode };
		listName = mode === 'rename' ? (currentList?.name ?? '') : '';
		droppingOn = null;
	}

	function saveList(event: SubmitEvent): void {
		event.preventDefault();
		const name = listName.trim();
		if (!name) return;
		const mode = naming;
		namingOn = null;
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
			location.hash = hashHref('list', made.id);
		});
	}

	function dropList(): void {
		if (current === null) return;
		if (!dropping) {
			droppingOn = current;
			return;
		}
		const id = current;
		droppingOn = null;
		run(async () => {
			await data('todos', `list?id=eq.${id}`, { method: 'DELETE' });
			leave();
		});
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
	});
</script>

{#snippet node(todo: Todo)}
	{@const kids = children.get(todo.id) ?? []}
	{@const shut = folded.has(todo.id)}
	<li>
		<div class="row" class:done={todo.done_at}>
			{#if kids.length}
				<button
					class="icon"
					aria-label="Subtasks of {todo.title}"
					aria-expanded={!shut}
					onclick={() => fold(todo.id)}>{shut ? '▸' : '▾'}</button
				>
			{:else}
				<span></span>
			{/if}
			<input
				type="checkbox"
				aria-label="Done: {todo.title}"
				checked={!!todo.done_at}
				onchange={() => toggle(todo)}
			/>
			<button class="title" aria-expanded={open === todo.id} onclick={() => (open = open === todo.id ? null : todo.id)}>
				<span class="text">{todo.title}</span>
				{#if shut}<span class="count">{count(todo.id)}<span class="visually-hidden"> subtasks</span></span>{/if}
				{#if todo.notes}<span class="count" aria-hidden="true">✎</span><span class="visually-hidden">, has notes</span>{/if}
			</button>
			<button
				class="icon"
				aria-label="Add a subtask to {todo.title}"
				aria-expanded={adding === todo.id}
				onclick={() => startSub(todo)}>+</button
			>
		</div>
		{#if open === todo.id}
			<div class="detail">
				<textarea
					aria-label="Notes for {todo.title}"
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
						<select
							value={todo.parent_id === null ? String(todo.list_id ?? '') : '-'}
							onchange={(e) => moveTo(todo, e.currentTarget.value)}
						>
							{#if todo.parent_id !== null}<option value="-" disabled>Choose a list</option>{/if}
							<option value="">Inbox</option>
							{#each lists as l (l.id)}
								<option value={String(l.id)}>{l.name}</option>
							{/each}
						</select>
					</label>
				{/if}
				<div class="actions">
					{#if deleting === todo.id}
						<button class="danger" onclick={() => remove(todo)}
							>Delete “{todo.title}” with {count(todo.id)} subtasks</button
						>
						<button onclick={() => (deleting = null)}>Keep</button>
					{:else}
						<button class="danger" aria-label="Delete {todo.title}" onclick={() => remove(todo)}
						>Delete</button
					>
					{/if}
				</div>
			</div>
		{/if}
		{#if adding === todo.id}
			<form class="actions sub" onsubmit={(e) => addSub(e, todo)}>
				<input
					bind:value={subtitle}
					aria-label="New subtask of {todo.title}"
					placeholder="New subtask"
					maxlength="500"
					{@attach (el) => el.focus()}
				/>
				<button type="submit">Add</button>
			</form>
		{/if}
		{#if kids.length && !shut}
			<ul class="plain">
				{#each kids as kid (kid.id)}
					{@render node(kid)}
				{/each}
			</ul>
		{/if}
	</li>
{/snippet}

<nav class="strip" aria-label="Lists">
	<a class="list" href={hashHref()} aria-current={current === null ? 'page' : undefined}>
		Inbox <span class="count">{openCount.get(null) ?? 0}</span>
	</a>
	{#each lists as l (l.id)}
		<a
			class="list"
			href={hashHref('list', l.id)}
			aria-current={current === l.id ? 'page' : undefined}
		>
			{l.name} <span class="count">{openCount.get(l.id) ?? 0}</span>
		</a>
	{/each}
	<button class="list" aria-expanded={naming === 'new'} onclick={() => startNaming('new')}
		>+ List</button
	>
</nav>

{#if tags.length}
	<div class="strip" role="group" aria-label="Filter by tag">
		{#each tags as t (t.id)}
			<button
				class="tag"
				aria-pressed={filter === t.id}
				style:--chip={chipColor(t.color)}
				onclick={() => filterBy(t.id)}>{t.name}</button
			>
		{/each}
		{#if filterTag}
			<button class="tag clear" onclick={() => filterBy(null)}>Show all</button>
		{/if}
	</div>
{/if}

{#if currentList}
	<div class="actions list-actions">
		<button aria-expanded={naming === 'rename'} onclick={() => startNaming('rename')}
			>Rename</button
		>
		{#if dropping}
			<button class="danger" onclick={dropList}
				>Delete “{currentList.name}” with {inList} {inList === 1 ? 'todo' : 'todos'}</button
			>
			<button onclick={() => (droppingOn = null)}>Keep</button>
		{:else}
			<button class="danger" onclick={dropList}>Delete list</button>
		{/if}
	</div>
{/if}

{#if naming}
	<form class="actions naming" onsubmit={saveList}>
		<input
			bind:value={listName}
			aria-label={naming === 'new' ? 'New list name' : 'List name'}
			placeholder={naming === 'new' ? 'New list, e.g. Shopping' : 'List name'}
			maxlength="200"
			{@attach (el) => el.focus()}
		/>
		<button type="submit">{naming === 'new' ? 'Make' : 'Save'}</button>
	</form>
{/if}

{#if !missing}
	<form class="actions" onsubmit={add}>
		<input
			bind:value={title}
			aria-label={currentList ? `New todo in ${currentList.name}` : 'New todo'}
			placeholder={currentList ? `New todo in ${currentList.name}` : 'New todo'}
			maxlength="500"
		/>
		<button type="submit">Add</button>
	</form>
{/if}

{#if error}<p class="error" role="alert">{error}</p>{/if}

{#if missing}
	<p>No such list. <a href={hashHref()}>Show the Inbox</a></p>
{:else if todos === null}
	<p class="muted">Loading…</p>
{:else if !shown.length}
	<p class="muted">{filterTag ? `Nothing here carries “${filterTag.name}”.` : 'Nothing to do here.'}</p>
{/if}

<ul class="plain tree">
	{#each children.get(null) ?? [] as todo (todo.id)}
		{@render node(todo)}
	{/each}
</ul>

<style>
	/* The fold button and the checkbox: the detail lines up under the title. */
	:host {
		--lead: 5rem;
	}
	form.actions input {
		flex: 1;
	}
	ul ul {
		padding-left: 0.75rem;
		border-left: 1px solid var(--hc-border);
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
		border-bottom: 1px solid var(--hc-border);
	}
	.title {
		display: flex;
		align-items: center;
		gap: 0.5rem;
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
		color: var(--hc-muted);
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
		padding: 0.5rem 0 1rem var(--lead);
	}
	.sub {
		padding: 0.5rem 0 0.5rem var(--lead);
	}
	.strip {
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
		min-height: 3rem;
		padding: 0 1rem;
		white-space: nowrap;
		color: inherit;
		text-decoration: none;
		border: 1px solid var(--hc-muted);
		border-radius: 0.5rem;
	}
	.list[aria-current='page'] {
		border-color: var(--hc-accent);
		color: var(--hc-accent);
		font-weight: bold;
	}
	.tag {
		flex: none;
		white-space: nowrap;
		border-radius: 999px;
		border-color: var(--chip);
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
</style>
