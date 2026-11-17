<svelte:options customElement={{ tag: 'heycarl-departures', extend: withBase }} />

<script lang="ts">
	import { onMount } from 'svelte';
	import { data, json } from '@heycarl/sdk';
	import ErrorNotice from '@heycarl/sdk/ErrorNotice.svelte';
	import { withBase } from '@heycarl/sdk/base';
	import { hashHref, hashId, leave } from '@heycarl/sdk/route';
	import Related from '@heycarl/sdk/Related.svelte';
	import TagChips from '@heycarl/sdk/TagChips.svelte';
	import Board from './Board.svelte';
	import StopForm from './StopForm.svelte';
	import { MODES, SCOPE, type Stop, type StopBody } from './stops.ts';

	// null until the first answer.
	let stops = $state<Stop[] | null>(null);
	let error = $state<unknown>(null);
	let open = $derived(hashId('stop'));
	let current = $derived(stops?.find((s) => s.id === open) ?? null);
	let missing = $derived(stops !== null && open !== null && !current);
	// The stop being edited, by id, so another stop never opens in edit.
	let editing = $state<number | null>(null);

	async function run(action: () => Promise<unknown>): Promise<void> {
		try {
			error = null;
			await action();
		} catch (e) {
			error = e;
		}
	}

	async function load(): Promise<void> {
		stops = await data<Stop[]>(SCOPE, 'stop?order=name,id');
	}

	async function create(body: StopBody): Promise<void> {
		const [made] = await data<Stop[]>(SCOPE, 'stop', json('POST', body, 'return=representation'));
		await load();
		location.hash = hashHref('stop', made.id);
	}

	async function save(s: Stop, body: StopBody): Promise<void> {
		await data(SCOPE, `stop?id=eq.${s.id}`, json('PATCH', body));
		editing = null;
		await load();
	}

	function remove(s: Stop): void {
		run(async () => {
			await data(SCOPE, `stop?id=eq.${s.id}`, { method: 'DELETE' });
			leave();
			await load();
		});
	}

	const mode = (s: Stop) => (s.transport ? (MODES[s.transport] ?? s.transport) : 'Every mode');
	const lines = (s: Stop) => (s.lines.length ? s.lines.join(', ') : 'Every line');
	const direction = (s: Stop) => (s.direction ? `One way (SL direction ${s.direction})` : 'Both');

	onMount(() => {
		run(load);
	});
</script>

<ErrorNotice {error} />

{#if missing}
	<p>No such stop. <a href={hashHref()}>Show all stops</a></p>
{:else if current}
	{@const s = current}
	<p><a href={hashHref()}>← All stops</a></p>
	<h2>{s.name}</h2>
	{#if editing === s.id}
		{#key s.id}
			<StopForm
				stop={s}
				submitLabel="Save"
				onsubmit={(body) => save(s, body)}
				oncancel={() => (editing = null)}
			/>
		{/key}
	{:else}
		<Board siteId={s.site_id} filter={s} limit={20} />
		<dl>
			<dt>Site</dt>
			<dd>{s.site_name}</dd>
			<dt>Mode</dt>
			<dd>{mode(s)}</dd>
			<dt>Lines</dt>
			<dd>{lines(s)}</dd>
			<dt>Direction</dt>
			<dd>{direction(s)}</dd>
		</dl>
		<div class="actions">
			<button onclick={() => (editing = s.id)}>Edit</button>
			<button onclick={() => remove(s)}>Delete</button>
		</div>
	{/if}
	<p class="muted">
		Link this stop to a place, or give both the same tag, to see its departures under “What’s
		here?” in Places.
	</p>
	<TagChips scope={SCOPE} table="stop" key={{ id: s.id }} />
	<Related scope={SCOPE} table="stop" key={{ id: s.id }} />
{:else}
	<section aria-labelledby="stops">
		<h2 id="stops">Stops</h2>
		{#if stops === null}
			<p class="muted">Loading…</p>
		{:else if !stops.length}
			<p class="muted">No stops yet. Add one below.</p>
		{:else}
			{#each stops as s (s.id)}
				<article aria-labelledby="stop-{s.id}">
					<h3 id="stop-{s.id}"><a class="row-link" href={hashHref('stop', s.id)}>{s.name}</a></h3>
					<Board siteId={s.site_id} filter={s} limit={4} />
				</article>
			{/each}
		{/if}
	</section>

	<section aria-labelledby="new">
		<h2 id="new">New stop</h2>
		<StopForm submitLabel="Add" onsubmit={create} />
	</section>
{/if}

<style>
	section,
	article {
		margin-bottom: 1.5rem;
	}
	dl {
		display: grid;
		grid-template-columns: max-content 1fr;
		gap: 0.25rem 1rem;
	}
	dd {
		margin: 0;
	}
</style>
