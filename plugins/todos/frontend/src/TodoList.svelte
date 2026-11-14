<svelte:options customElement="heycarl-todos" />

<script lang="ts">
	import { onMount } from 'svelte';
	import { data, json } from '@heycarl/sdk';

	interface Todo {
		id: number;
		title: string;
		notes: string;
		due: string | null;
		done_at: string | null;
	}

	let todos = $state<Todo[]>([]);
	let title = $state('');
	let error = $state<string | null>(null);

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

	function toggle(todo: Todo): void {
		const done_at = todo.done_at ? null : new Date().toISOString();
		run(() => data('todos', `todo?id=eq.${todo.id}`, json('PATCH', { done_at })));
	}

	function remove(todo: Todo): void {
		run(() => data('todos', `todo?id=eq.${todo.id}`, { method: 'DELETE' }));
	}

	onMount(() => {
		load().catch((e) => (error = String(e)));
	});
</script>

<form onsubmit={add}>
	<input bind:value={title} placeholder="New todo" maxlength="500" />
	<button type="submit">Add</button>
</form>

{#if error}<p class="error">{error}</p>{/if}

<ul>
	{#each todos as todo (todo.id)}
		<li class:done={todo.done_at}>
			<label>
				<input type="checkbox" checked={!!todo.done_at} onchange={() => toggle(todo)} />
				{todo.title}
			</label>
			<button class="remove" onclick={() => remove(todo)} aria-label="Delete">×</button>
		</li>
	{/each}
</ul>

<style>
	form {
		display: flex;
		gap: 0.5rem;
	}
	input:not([type]) {
		flex: 1;
		font: inherit;
		padding: 0.5rem;
	}
	ul {
		list-style: none;
		padding: 0;
	}
	li {
		display: flex;
		justify-content: space-between;
		padding: 0.5rem 0;
	}
	.done {
		opacity: 0.5;
		text-decoration: line-through;
	}
	.remove {
		background: none;
		border: none;
		color: inherit;
		font-size: 1.25rem;
	}
	.error {
		color: var(--hc-error, red);
	}
</style>
