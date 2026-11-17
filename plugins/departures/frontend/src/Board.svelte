<!--
	The next departures from one stop, refreshed while it is shown and the
	page is visible. Minutes count from the expected time at render, so a
	cached answer never shows a stale "2 min".
-->
<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import ErrorNotice from '@heycarl/sdk/ErrorNotice.svelte';
	import { board, boardUrl, clock, departureKey, type Board, type Departure, type Filter } from './stops.ts';

	const REFRESH_MS = 30_000;
	const TICK_MS = 10_000;

	let { siteId, filter, limit = 6 }: { siteId: number; filter: Filter; limit?: number } = $props();

	// null until the first answer.
	let current = $state<Board | null>(null);
	let error = $state<unknown>(null);
	let now = $state(Date.now());
	let shown = $derived(current?.departures.slice(0, limit) ?? []);

	let generation = 0;
	async function load(url: string): Promise<void> {
		const mine = ++generation;
		try {
			const answer = await board(url);
			if (mine !== generation) return;
			current = answer;
			error = null;
		} catch (e) {
			if (mine === generation) error = e;
		}
	}

	$effect(() => {
		const url = boardUrl(siteId, filter);
		untrack(() => {
			current = null;
			load(url);
		});
		const timer = setInterval(() => {
			if (!document.hidden) load(url);
		}, REFRESH_MS);
		return () => clearInterval(timer);
	});

	onMount(() => {
		const timer = setInterval(() => (now = Date.now()), TICK_MS);
		return () => clearInterval(timer);
	});

	function when(d: Departure): string {
		const minutes = Math.round((Date.parse(d.expected) - now) / 60_000);
		if (minutes <= 0) return 'Now';
		if (minutes < 60) return `${minutes} min`;
		return clock(d.expected);
	}

	const late = (d: Departure) =>
		Math.round((Date.parse(d.expected) - Date.parse(d.scheduled)) / 60_000);
</script>

<ErrorNotice {error} />
{#if current === null}
	{#if error == null}<p class="muted">Loading…</p>{/if}
{:else}
	{#each current.notices as notice (notice)}
		<p class="notice">{notice}</p>
	{/each}
	{#if !shown.length}
		<p class="muted">No departures in the next hour.</p>
	{:else}
		<ol class="plain departures">
			{#each shown as d (departureKey(d))}
				{@const cancelled = d.state === 'CANCELLED'}
				<li>
					<span class="line">{d.designation}</span>
					<span class="destination" class:cancelled>{d.destination}</span>
					{#if cancelled}
						<span class="when">Cancelled</span>
					{:else}
						<time class="when" datetime={d.expected}>{when(d)}</time>
					{/if}
					{#if !cancelled && late(d) > 0}
						<span class="muted detail">{late(d)} min late, due {clock(d.scheduled)}</span>
					{/if}
					{#each d.notices as notice (notice)}
						<span class="muted detail">{notice}</span>
					{/each}
				</li>
			{/each}
		</ol>
	{/if}
{/if}

<style>
	.departures li {
		display: grid;
		grid-template-columns: 3rem 1fr auto;
		align-items: baseline;
		gap: 0 0.5rem;
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--hc-border);
	}
	.line {
		font-weight: 600;
		text-align: center;
		border: 1px solid var(--hc-muted);
		border-radius: 0.5rem;
	}
	.destination {
		overflow-wrap: anywhere;
	}
	.cancelled {
		text-decoration: line-through;
	}
	.when {
		font-variant-numeric: tabular-nums;
		text-align: end;
	}
	.detail {
		grid-column: 2 / -1;
	}
	.notice {
		border-inline-start: 0.25rem solid var(--hc-muted);
		padding-inline-start: 0.5rem;
	}
</style>
