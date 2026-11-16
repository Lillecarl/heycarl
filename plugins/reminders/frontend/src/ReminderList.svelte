<svelte:options customElement={{ tag: 'heycarl-reminders', extend: withBase }} />

<script lang="ts">
	import { onMount } from 'svelte';
	import { data, json } from '@heycarl/sdk';
	import { withBase } from '@heycarl/sdk/base';
	import Related from '@heycarl/sdk/Related.svelte';

	interface Reminder {
		id: number;
		title: string;
		body: string;
		due_at: string;
		state: 'pending' | 'sending' | 'sent' | 'failed';
		last_error: string | null;
	}

	let reminders = $state<Reminder[]>([]);
	let title = $state('');
	let when = $state(inAnHour());
	let error = $state<string | null>(null);
	let open = $state<number | null>(null);

	// datetime-local wants local time without a zone.
	function inAnHour(): string {
		const d = new Date(Date.now() + 3600_000);
		d.setSeconds(0, 0);
		return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
	}

	async function load(): Promise<void> {
		reminders = await data<Reminder[]>('reminders', 'reminder?order=due_at.desc&limit=100');
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
		if (!text || !when) return;
		const due_at = new Date(when).toISOString();
		title = '';
		run(() => data('reminders', 'reminder', json('POST', { title: text, due_at })));
	}

	function remove(r: Reminder): void {
		run(() => data('reminders', `reminder?id=eq.${r.id}`, { method: 'DELETE' }));
	}

	const format = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });

	onMount(() => {
		load().catch((e) => (error = String(e)));
		const timer = setInterval(() => load().catch(() => {}), 30_000);
		return () => clearInterval(timer);
	});
</script>

<form onsubmit={add}>
	<input class="title" bind:value={title} placeholder="Remind me to…" maxlength="200" />
	<input type="datetime-local" bind:value={when} />
	<button type="submit">Add</button>
</form>

{#if error}<p class="error">{error}</p>{/if}

<ul>
	{#each reminders as r (r.id)}
		<li class={r.state}>
			<span class="when">{format.format(new Date(r.due_at))}</span>
			<span class="what" title={r.last_error ?? ''}>{r.title}</span>
			<span class="state">{r.state}</span>
			<button
				class="remove"
				aria-label="Related"
				aria-expanded={open === r.id}
				onclick={() => (open = open === r.id ? null : r.id)}>🔗</button
			>
			<button class="remove" onclick={() => remove(r)} aria-label="Delete">×</button>
			{#if open === r.id}
				<div class="related"><Related scope="reminders" table="reminder" key={{ id: r.id }} /></div>
			{/if}
		</li>
	{/each}
</ul>

<style>
	form {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}
	input {
		font: inherit;
		padding: 0.5rem;
	}
	.title {
		flex: 1 1 12rem;
	}
	ul {
		list-style: none;
		padding: 0;
	}
	li {
		display: grid;
		grid-template-columns: auto 1fr auto auto auto;
		gap: 0.75rem;
		align-items: center;
		padding: 0.5rem 0;
	}
	.when,
	.state {
		opacity: 0.7;
		font-size: 0.9em;
	}
	.sent {
		opacity: 0.5;
	}
	.failed .state {
		color: var(--hc-error, red);
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
	.related {
		grid-column: 1 / -1;
	}
</style>
