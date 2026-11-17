<svelte:options customElement={{ tag: 'heycarl-places', extend: withBase }} />

<script lang="ts">
	import { onMount } from 'svelte';
	import { data, json, pluginPages } from '@heycarl/sdk';
	import Embed from '@heycarl/sdk/Embed.svelte';
	import ErrorNotice from '@heycarl/sdk/ErrorNotice.svelte';
	import { withBase } from '@heycarl/sdk/base';
	import { position, type Position } from '@heycarl/sdk/position';
	import { hashHref, hashId, leave } from '@heycarl/sdk/route';
	import Related from '@heycarl/sdk/Related.svelte';
	import TagChips from '@heycarl/sdk/TagChips.svelte';

	interface Place {
		id: number;
		name: string;
		latitude: number;
		longitude: number;
		radius: number;
		notes: string;
	}
	interface Near extends Place {
		distance: number;
		inside: boolean;
	}
	interface Entry {
		scope: string;
		table: string;
		key: Record<string, unknown>;
		label: string;
		url: string | null;
	}
	interface Bound {
		place_id: number;
		place_name: string;
		via_tag: string | null;
		entry: Entry;
	}
	interface Form {
		name: string;
		latitude: number | null;
		longitude: number | null;
		radius: number;
		notes: string;
	}

	const SCOPE = 'places';
	const NEARBY = 5000;
	const blank = (): Form => ({ name: '', latitude: null, longitude: null, radius: 100, notes: '' });

	// null until the first answer.
	let places = $state<Place[] | null>(null);
	let error = $state<unknown>(null);
	let pages = $state<Record<string, string>>({});
	let open = $derived(hashId('place'));
	let current = $derived(places?.find((p) => p.id === open) ?? null);
	let missing = $derived(places !== null && open !== null && !current);

	let locating = $state(false);
	let here = $state<{ at: Position; near: Near[]; bound: Bound[] } | null>(null);
	let inside = $derived(here?.near.filter((p) => p.inside) ?? []);
	let around = $derived(here?.near.filter((p) => !p.inside) ?? []);

	let draft = $state<Form>(blank());
	// Keyed by place, so opening another place shows no stale form.
	let edit = $state<(Form & { id: number }) | null>(null);

	const rpc = <T,>(fn: string, args: object) => data<T>(SCOPE, `rpc/${fn}`, json('POST', args));
	const href = (e: Entry) => e.url ?? pages[e.scope] ?? '/';
	const boundTo = (id: number) => here?.bound.filter((b) => b.place_id === id) ?? [];
	const fixed = (n: number) => n.toFixed(5);
	const metres = (m: number) =>
		m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(m < 10_000 ? 1 : 0)} km`;
	const osm = (p: { latitude: number; longitude: number }) =>
		`https://www.openstreetmap.org/?mlat=${fixed(p.latitude)}&mlon=${fixed(p.longitude)}` +
		`#map=17/${fixed(p.latitude)}/${fixed(p.longitude)}`;

	async function run(action: () => Promise<unknown>): Promise<void> {
		try {
			error = null;
			await action();
		} catch (e) {
			error = e;
		}
	}

	async function load(): Promise<void> {
		places = await data<Place[]>(SCOPE, 'place?order=name,id');
	}

	// The position goes in POST bodies only, and lives in this page's memory.
	function look(): void {
		locating = true;
		run(async () => {
			try {
				const at = await position();
				const point = { lat: at.latitude, lon: at.longitude };
				const [near, bound] = await Promise.all([
					rpc<Near[]>('near', { ...point, within: NEARBY }),
					rpc<Bound[]>('here', point)
				]);
				here = { at, near, bound };
			} finally {
				locating = false;
			}
		});
	}

	function fill(form: Form): void {
		locating = true;
		run(async () => {
			try {
				const at = await position();
				form.latitude = Number(fixed(at.latitude));
				form.longitude = Number(fixed(at.longitude));
			} finally {
				locating = false;
			}
		});
	}

	function body(form: Form) {
		return {
			name: form.name.trim(),
			latitude: form.latitude,
			longitude: form.longitude,
			radius: form.radius,
			notes: form.notes.trim()
		};
	}

	function create(event: SubmitEvent): void {
		event.preventDefault();
		const wanted = body(draft);
		run(async () => {
			const [made] = await data<Place[]>(
				SCOPE,
				'place',
				json('POST', wanted, 'return=representation')
			);
			draft = blank();
			await load();
			location.hash = hashHref('place', made.id);
		});
	}

	function save(event: SubmitEvent, p: Place): void {
		event.preventDefault();
		if (!edit) return;
		const changes = body(edit);
		run(async () => {
			await data(SCOPE, `place?id=eq.${p.id}`, json('PATCH', changes));
			edit = null;
			await load();
		});
	}

	function remove(p: Place): void {
		run(async () => {
			await data(SCOPE, `place?id=eq.${p.id}`, { method: 'DELETE' });
			leave();
			here = null;
			await load();
		});
	}

	onMount(() => {
		run(load);
		run(async () => (pages = await pluginPages()));
	});
</script>

{#snippet fields(form: Form)}
	<label>Name <input bind:value={form.name} maxlength="100" required /></label>
	<div class="point">
		<label
			>Latitude
			<input
				type="number"
				inputmode="decimal"
				step="any"
				min="-90"
				max="90"
				required
				bind:value={form.latitude}
			/></label
		>
		<label
			>Longitude
			<input
				type="number"
				inputmode="decimal"
				step="any"
				min="-180"
				max="180"
				required
				bind:value={form.longitude}
			/></label
		>
	</div>
	<button type="button" disabled={locating} onclick={() => fill(form)}>Use my location</button>
	<label
		>Radius in metres
		<input type="number" inputmode="numeric" min="10" max="50000" required bind:value={form.radius} />
	</label>
	<label>Notes <textarea bind:value={form.notes} maxlength="2000"></textarea></label>
{/snippet}

<ErrorNotice {error} />
{#if locating}<p class="muted" role="status">Finding your location…</p>{/if}

{#if missing}
	<p>No such place. <a href={hashHref()}>Show all places</a></p>
{:else if current}
	{@const p = current}
	<p><a href={hashHref()}>← All places</a></p>
	<h2>{p.name}</h2>
	{#if edit?.id === p.id}
		<form class="form" onsubmit={(e) => save(e, p)}>
			{@render fields(edit)}
			<div class="actions">
				<button type="submit" class="primary">Save</button>
				<button type="button" onclick={() => (edit = null)}>Cancel</button>
			</div>
		</form>
	{:else}
		<dl>
			<dt>Position</dt>
			<dd>
				{fixed(p.latitude)}, {fixed(p.longitude)}
				(<a href={osm(p)} target="_blank" rel="noreferrer">map</a>)
			</dd>
			<dt>Radius</dt>
			<dd>{metres(p.radius)}</dd>
			{#if p.notes}
				<dt>Notes</dt>
				<dd class="notes">{p.notes}</dd>
			{/if}
		</dl>
		<div class="actions">
			<button onclick={() => (edit = { ...p })}>Edit</button>
			<button onclick={() => remove(p)}>Delete</button>
		</div>
	{/if}
	<p class="muted">
		Link an entry to this place, or give the entry and the place the same tag. It then shows
		under “What’s here?” when you are within the radius.
	</p>
	<TagChips scope={SCOPE} table="place" key={{ id: p.id }} />
	<Related scope={SCOPE} table="place" key={{ id: p.id }} />
{:else}
	<section aria-labelledby="here">
		<h2 id="here">Here</h2>
		<button class="primary" disabled={locating} onclick={look}>What’s here?</button>
		{#if here}
			<p class="muted">Your location is accurate to about {metres(here.at.accuracy)}.</p>
			{#if !inside.length}
				<p>You are not at any of your places.</p>
			{/if}
			{#each inside as p (p.id)}
				<h3><a href={hashHref('place', p.id)}>{p.name}</a></h3>
				{#if boundTo(p.id).length}
					<ul class="plain bound">
						{#each boundTo(p.id) as b (`${b.entry.scope}/${b.entry.table}/${JSON.stringify(b.entry.key)}`)}
							<li>
								<a href={href(b.entry)}>{b.entry.label}</a>
								{#if b.via_tag}<span class="muted">tagged {b.via_tag}</span>{/if}
								<div class="embed">
									<Embed scope={b.entry.scope} table={b.entry.table} key={b.entry.key} />
								</div>
							</li>
						{/each}
					</ul>
				{:else}
					<p class="muted">Nothing is linked to {p.name}.</p>
				{/if}
			{/each}
			{#if around.length}
				<h3>Within {metres(NEARBY)}</h3>
				<ul class="plain near">
					{#each around as p (p.id)}
						<li>
							<a href={hashHref('place', p.id)}>{p.name}</a>
							<span class="muted">{metres(p.distance)}</span>
						</li>
					{/each}
				</ul>
			{/if}
		{/if}
	</section>

	<section aria-labelledby="places">
		<h2 id="places">Places</h2>
		{#if places === null}
			<p class="muted">Loading…</p>
		{:else if !places.length}
			<p class="muted">No places yet. Add one below.</p>
		{:else}
			<ul class="plain near">
				{#each places as p (p.id)}
					<li>
						<a href={hashHref('place', p.id)}>{p.name}</a>
						<span class="muted">{metres(p.radius)}</span>
					</li>
				{/each}
			</ul>
		{/if}
	</section>

	<section aria-labelledby="new">
		<h2 id="new">New place</h2>
		<form class="form" onsubmit={create}>
			{@render fields(draft)}
			<div class="actions">
				<button type="submit" class="primary">Add</button>
			</div>
		</form>
	</section>
{/if}

<style>
	section {
		margin-bottom: 1.5rem;
	}
	.form {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.form label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.point {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
		gap: 0.5rem;
	}
	.near li,
	.bound li {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		gap: 0.5rem;
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--hc-border);
	}
	.embed {
		flex-basis: 100%;
	}
	.embed:empty {
		display: none;
	}
	dl {
		display: grid;
		grid-template-columns: max-content 1fr;
		gap: 0.25rem 1rem;
	}
	dd {
		margin: 0;
	}
	.notes {
		white-space: pre-line;
		overflow-wrap: anywhere;
	}
</style>
