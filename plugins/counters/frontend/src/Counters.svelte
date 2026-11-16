<svelte:options customElement="heycarl-counters" />

<script lang="ts">
	import { onMount } from 'svelte';
	import { data, json } from '@heycarl/sdk';
	import Related from '@heycarl/sdk/Related.svelte';

	interface Counter {
		id: number;
		name: string;
		unit: string;
		notes: string;
		step: number;
		value: number;
		events: number;
		last_at: string | null;
	}
	interface Change {
		id: number;
		delta: number;
		note: string;
		at: string;
	}
	interface Day {
		day: string;
		total: number;
		added: number;
		removed: number;
		events: number;
	}

	const SCOPE = 'counters';
	const DAYS = 14;
	const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;

	let counters = $state<Counter[]>([]);
	let error = $state<string | null>(null);
	let name = $state('');
	let unit = $state('');
	let step = $state(1);
	// The view lives in the hash (#/counter/12), so a link to a counter opens it.
	let open = $state<number | null>(parse(location.hash));
	let events = $state<Change[]>([]);
	let days = $state<Day[]>([]);
	let amount = $state<number | null>(null);
	let note = $state('');
	let target = $state<number | null>(null);
	let edit = $state<{ name: string; unit: string; notes: string; step: number } | null>(null);
	let deleting = $state(false);

	let current = $derived(counters.find((c) => c.id === open) ?? null);
	let peak = $derived(Math.max(1, ...days.map((d) => Math.abs(d.total))));

	function parse(hash: string): number | null {
		const m = /^#\/counter\/(\d+)$/.exec(hash);
		return m ? Number(m[1]) : null;
	}

	const rpc = <T,>(fn: string, args: object) => data<T>(SCOPE, `rpc/${fn}`, json('POST', args));

	// Taps overlap; only the newest load may replace the list, or an older
	// answer that arrives late would show a stale value.
	let generation = 0;

	async function load(): Promise<void> {
		const mine = ++generation;
		const rows = await data<Counter[]>(SCOPE, 'counter_value?order=name,id');
		if (mine !== generation) return;
		counters = rows;
		if (open !== null) await loadDetail(open);
	}

	async function loadDetail(id: number): Promise<void> {
		const today = new Date();
		const since = new Date(today.getTime() - (DAYS - 1) * 86_400_000);
		const [e, d] = await Promise.all([
			data<Change[]>(SCOPE, `event?counter_id=eq.${id}&order=id.desc&limit=20`),
			data<Day[]>(
				SCOPE,
				`rpc/daily_totals?counter_id=${id}&since=${isoDay(since)}&until=${isoDay(today)}` +
					`&zone=${encodeURIComponent(zone)}`
			)
		]);
		if (open === id) {
			events = e;
			days = [...d].reverse();
		}
	}

	function isoDay(d: Date): string {
		return new Intl.DateTimeFormat('sv-SE', { timeZone: zone }).format(d);
	}

	async function run(action: () => Promise<unknown>): Promise<void> {
		try {
			error = null;
			await action();
		} catch (e) {
			error = String(e);
		}
		try {
			await load();
		} catch (e) {
			error = String(e);
		}
	}

	function bump(c: Counter, sign: 1 | -1): void {
		const delta = sign * c.step;
		// Shown at once; the reload after the write corrects it.
		c.value += delta;
		run(() => rpc('bump', { counter_id: c.id, delta }));
	}

	function addAmount(event: SubmitEvent, c: Counter): void {
		event.preventDefault();
		if (!amount) return;
		const delta = amount;
		const text = note.trim();
		amount = null;
		note = '';
		run(() => rpc('bump', { counter_id: c.id, delta, note: text }));
	}

	function setTo(event: SubmitEvent, c: Counter): void {
		event.preventDefault();
		if (target === null) return;
		const value = target;
		target = null;
		run(() => rpc('set_value', { counter_id: c.id, target: value }));
	}

	function create(event: SubmitEvent): void {
		event.preventDefault();
		const text = name.trim();
		if (!text) return;
		const body = { name: text, unit: unit.trim(), step: step > 0 ? step : 1 };
		name = '';
		unit = '';
		step = 1;
		run(() => data(SCOPE, 'counter', json('POST', body)));
	}

	function save(event: SubmitEvent, c: Counter): void {
		event.preventDefault();
		if (!edit) return;
		const body = { ...edit, name: edit.name.trim() };
		edit = null;
		run(() => data(SCOPE, `counter?id=eq.${c.id}`, json('PATCH', body)));
	}

	function remove(c: Counter): void {
		deleting = false;
		// Not location.hash = '': that reloads the page and aborts the DELETE.
		run(async () => {
			await data(SCOPE, `counter?id=eq.${c.id}`, { method: 'DELETE' });
			location.hash = '#/';
		});
	}

	function toggle(c: Counter): void {
		location.hash = open === c.id ? '#/' : `#/counter/${c.id}`;
	}

	function show(id: number | null): void {
		open = id;
		events = [];
		days = [];
		edit = null;
		deleting = false;
		amount = null;
		target = null;
		if (id !== null) loadDetail(id).catch((e) => (error = String(e)));
	}

	const signed = (n: number) => (n > 0 ? `+${n}` : String(n));
	const when = (at: string) =>
		new Date(at).toLocaleString(undefined, {
			weekday: 'short',
			day: 'numeric',
			month: 'short',
			hour: '2-digit',
			minute: '2-digit'
		});

	onMount(() => {
		load().catch((e) => (error = String(e)));
		const onHash = () => show(parse(location.hash));
		addEventListener('hashchange', onHash);
		return () => removeEventListener('hashchange', onHash);
	});
</script>

{#if error}<p class="error" role="alert">{error}</p>{/if}

<ul class="counters">
	{#each counters as c (c.id)}
		<li class="card" class:open={open === c.id}>
			<button class="minus big" aria-label="Minus {c.step} on {c.name}" onclick={() => bump(c, -1)}
				>−</button
			>
			<button class="middle" aria-expanded={open === c.id} onclick={() => toggle(c)}>
				<span class="name">{c.name}</span>
				<span class="value">{c.value}</span>
				{#if c.unit}<span class="unit">{c.unit}</span>{/if}
			</button>
			<button class="plus big" aria-label="Plus {c.step} on {c.name}" onclick={() => bump(c, 1)}
				>+</button
			>
		</li>
		{#if open === c.id && current}
			<li class="detail">
				{#if current.notes}<p class="notes">{current.notes}</p>{/if}

				<form onsubmit={(e) => addAmount(e, c)}>
					<input
						type="number"
						inputmode="numeric"
						step="1"
						placeholder="Amount, e.g. -3"
						bind:value={amount}
					/>
					<input placeholder="Note" maxlength="500" bind:value={note} />
					<button type="submit">Add</button>
				</form>

				<form onsubmit={(e) => setTo(e, c)}>
					<input
						type="number"
						inputmode="numeric"
						step="1"
						placeholder="Set to"
						bind:value={target}
					/>
					<button type="submit">Set</button>
					<button type="button" onclick={() => run(() => rpc('reset_value', { counter_id: c.id }))}
						>Reset to 0</button
					>
				</form>

				<div class="actions">
					<button
						disabled={!events.length}
						onclick={() => run(() => rpc('undo_last', { counter_id: c.id }))}>Undo last</button
					>
					<button
						onclick={() =>
							(edit = edit ? null : { name: c.name, unit: c.unit, notes: c.notes, step: c.step })}
						>Edit</button
					>
					{#if deleting}
						<button class="danger" onclick={() => remove(c)}>Delete for real</button>
						<button onclick={() => (deleting = false)}>Keep</button>
					{:else}
						<button onclick={() => (deleting = true)}>Delete</button>
					{/if}
				</div>

				{#if edit}
					<form class="edit" onsubmit={(e) => save(e, c)}>
						<label>Name <input bind:value={edit.name} required maxlength="100" /></label>
						<label>Unit <input bind:value={edit.unit} maxlength="50" /></label>
						<label
							>Step <input type="number" inputmode="numeric" min="1" bind:value={edit.step} /></label
						>
						<label>Notes <textarea bind:value={edit.notes} maxlength="2000"></textarea></label>
						<button type="submit">Save</button>
					</form>
				{/if}

				<h3>Last {DAYS} days</h3>
				<ol class="days">
					{#each days as d (d.day)}
						<li>
							<span class="day">{d.day.slice(5)}</span>
							<span class="bar"
								><span
									class:down={d.total < 0}
									style:width="{(Math.abs(d.total) / peak) * 100}%"
								></span></span
							>
							<span class="total">{d.events ? signed(d.total) : ''}</span>
						</li>
					{/each}
				</ol>

				<h3>Recent</h3>
				{#if events.length}
					<ol class="events">
						{#each events as e (e.id)}
							<li>
								<span class="delta" class:down={e.delta < 0}>{signed(e.delta)}</span>
								<span class="note">{e.note}</span>
								<time datetime={e.at}>{when(e.at)}</time>
							</li>
						{/each}
					</ol>
				{:else}
					<p class="muted">Nothing counted yet.</p>
				{/if}

				<Related scope={SCOPE} table="counter" key={{ id: c.id }} />
			</li>
		{/if}
	{/each}
</ul>

<form class="new" onsubmit={create}>
	<input bind:value={name} placeholder="New counter" maxlength="100" required />
	<input bind:value={unit} placeholder="Unit" maxlength="50" class="short" />
	<label class="short">Step <input type="number" inputmode="numeric" min="1" bind:value={step} /></label>
	<button type="submit">Add</button>
</form>

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
	input,
	textarea {
		min-height: 3rem;
		padding: 0 0.75rem;
		box-sizing: border-box;
		border: 1px solid var(--hc-muted, gray);
		border-radius: 0.5rem;
		background: transparent;
		min-width: 0;
	}
	textarea {
		padding: 0.5rem 0.75rem;
		min-height: 5rem;
	}
	button {
		min-height: 3rem;
		min-width: 3rem;
		padding: 0 1rem;
		border: 1px solid var(--hc-muted, gray);
		border-radius: 0.5rem;
		background: transparent;
		cursor: pointer;
		touch-action: manipulation;
	}
	button:disabled {
		opacity: 0.4;
	}
	ul,
	ol {
		list-style: none;
		padding: 0;
		margin: 0;
	}
	.counters {
		display: grid;
		gap: 0.75rem;
	}
	.card {
		display: grid;
		grid-template-columns: 4.5rem 1fr 4.5rem;
		gap: 0.5rem;
		align-items: stretch;
	}
	.big {
		font-size: 2.5rem;
		line-height: 1;
		min-height: 5rem;
		padding: 0;
		user-select: none;
		-webkit-user-select: none;
	}
	.plus {
		border-color: var(--hc-accent, teal);
		color: var(--hc-accent, teal);
	}
	.middle {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		padding: 0.25rem 0.5rem;
		overflow: hidden;
	}
	.open .middle {
		border-color: var(--hc-accent, teal);
	}
	.name {
		max-width: 100%;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.value {
		font-size: 2rem;
		font-weight: bold;
		font-variant-numeric: tabular-nums;
	}
	.unit,
	.muted,
	.notes,
	time,
	.day {
		color: var(--hc-muted, gray);
	}
	.detail {
		display: grid;
		gap: 0.75rem;
		padding: 0 0 1rem;
		border-bottom: 1px solid var(--hc-muted, gray);
	}
	form,
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}
	form input {
		flex: 1 1 8rem;
	}
	.edit {
		flex-direction: column;
	}
	.edit label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.danger {
		border-color: var(--hc-error, red);
		color: var(--hc-error, red);
	}
	h3 {
		margin: 0.5rem 0 0;
		font-size: 1rem;
	}
	.days li {
		display: grid;
		grid-template-columns: 3.5rem 1fr 4rem;
		gap: 0.5rem;
		align-items: center;
		min-height: 1.5rem;
	}
	.bar span {
		display: block;
		height: 0.75rem;
		border-radius: 0.25rem;
		background: var(--hc-accent, teal);
	}
	.bar .down {
		background: var(--hc-error, red);
	}
	.total,
	.delta {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	.events li {
		display: grid;
		grid-template-columns: 4rem 1fr auto;
		gap: 0.5rem;
		padding: 0.25rem 0;
	}
	.delta {
		font-weight: bold;
		color: var(--hc-accent, teal);
	}
	.delta.down {
		color: var(--hc-error, red);
	}
	.note {
		overflow-wrap: anywhere;
	}
	.new {
		margin-top: 1.5rem;
	}
	.new .short {
		flex: 0 1 6rem;
	}
	.new label.short {
		display: flex;
		align-items: center;
		gap: 0.25rem;
		flex: 0 1 8rem;
	}
	.new label.short input {
		width: 4rem;
		flex: 1;
	}
	.error {
		color: var(--hc-error, red);
	}
	.events time {
		font-size: 0.875rem;
	}
</style>
