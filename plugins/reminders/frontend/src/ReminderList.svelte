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

	// null until the first answer.
	let reminders = $state<Reminder[] | null>(null);
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
		// The kernel sends due reminders; this shows their new state.
		const timer = setInterval(() => load().catch(() => {}), 30_000);
		return () => clearInterval(timer);
	});
</script>

<form class="actions" onsubmit={add}>
	<input
		class="title"
		bind:value={title}
		aria-label="Reminder"
		placeholder="Remind me to…"
		maxlength="200"
	/>
	<input type="datetime-local" aria-label="When" bind:value={when} />
	<button type="submit">Add</button>
</form>

{#if error}<p class="error" role="alert">{error}</p>{/if}

{#if reminders === null}
	<p class="muted">Loading…</p>
{:else if !reminders.length}
	<p class="muted">No reminders yet.</p>
{/if}

<ul class="plain">
	{#each reminders ?? [] as r (r.id)}
		<li class={r.state}>
			<span class="what">{r.title}</span>
			<span class="meta muted">
				<time datetime={r.due_at}>{format.format(new Date(r.due_at))}</time> · {r.state}
			</span>
			<span class="buttons">
				<button
					class="icon"
					aria-label="Links of {r.title}"
					aria-expanded={open === r.id}
					onclick={() => (open = open === r.id ? null : r.id)}>🔗</button
				>
				<button class="icon" aria-label="Delete {r.title}" onclick={() => remove(r)}>×</button>
			</span>
			{#if r.last_error}<p class="error why">{r.last_error}</p>{/if}
			{#if open === r.id}
				<div class="related"><Related scope="reminders" table="reminder" key={{ id: r.id }} /></div>
			{/if}
		</li>
	{/each}
</ul>

<style>
	.title {
		flex: 1 1 12rem;
	}
	ul {
		margin-top: 1rem;
	}
	li {
		display: grid;
		grid-template-columns: 1fr auto;
		grid-template-areas:
			'what buttons'
			'meta buttons';
		column-gap: 0.5rem;
		align-items: center;
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--hc-border);
	}
	.what {
		grid-area: what;
		overflow-wrap: anywhere;
	}
	.meta {
		grid-area: meta;
		font-size: 0.875rem;
	}
	.buttons {
		grid-area: buttons;
		display: flex;
	}
	.sent .what,
	.sent .meta {
		opacity: 0.6;
	}
	.failed .meta {
		color: var(--hc-error);
	}
	.why,
	.related {
		grid-column: 1 / -1;
		margin: 0.25rem 0 0;
	}
</style>
