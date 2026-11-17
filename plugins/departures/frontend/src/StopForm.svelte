<!--
	A stop's fields. A new stop searches for its SL site; an existing one
	keeps its site. The mode, line and direction choices come from the
	site's departures in the next hour, plus whatever the stop already has.
-->
<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import ErrorNotice from '@heycarl/sdk/ErrorNotice.svelte';
	import {
		board,
		boardUrl,
		destinations,
		EVERYTHING,
		MODES,
		searchSites,
		type Departure,
		type Direction,
		type Site,
		type Stop,
		type StopBody
	} from './stops.ts';

	let {
		stop,
		submitLabel,
		onsubmit,
		oncancel
	}: {
		stop?: Stop;
		submitLabel: string;
		onsubmit: (body: StopBody) => Promise<void>;
		oncancel?: () => void;
	} = $props();

	interface Form {
		name: string;
		site: Pick<Site, 'id' | 'name'> | null;
		transport: string;
		lines: number[];
		direction: Direction | null;
	}

	const initial = (s: Stop | undefined): Form =>
		s
			? {
					name: s.name,
					site: { id: s.site_id, name: s.site_name },
					transport: s.transport ?? '',
					lines: [...s.lines],
					direction: s.direction
				}
			: { name: '', site: null, transport: '', lines: [], direction: null };

	// The form starts from the stop it was opened with; the parent keys it by stop.
	const opened = untrack(() => stop);
	let form = $state<Form>(initial(opened));
	let error = $state<unknown>(null);
	let saving = $state(false);

	let query = $state('');
	// null: no search yet.
	let found = $state<Site[] | null>(null);
	// null: not loaded.
	let preview = $state<Departure[] | null>(null);

	let ofMode = $derived(
		(preview ?? []).filter((d) => !form.transport || d.transport === form.transport)
	);
	let modes = $derived(
		[...new Set([...(preview ?? []).map((d) => d.transport), form.transport])].filter(Boolean)
	);
	let lines = $derived.by(() => {
		const seen = new Map<number, string>(form.lines.map((l) => [l, String(l)]));
		for (const d of ofMode) seen.set(d.line, d.designation);
		return [...seen].sort(([a], [b]) => a - b);
	});
	let towards = $derived(
		destinations(ofMode.filter((d) => !form.lines.length || form.lines.includes(d.line)))
	);

	let searchGeneration = 0;
	let searchTimer: ReturnType<typeof setTimeout> | undefined;

	function queueSearch(): void {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(search, 250);
	}

	async function search(): Promise<void> {
		const mine = ++searchGeneration;
		const q = query.trim();
		try {
			const sites = q ? await searchSites(q) : null;
			if (mine === searchGeneration) found = sites;
		} catch (e) {
			if (mine === searchGeneration) error = e;
		}
	}

	let previewGeneration = 0;
	async function loadPreview(siteId: number): Promise<void> {
		const mine = ++previewGeneration;
		preview = null;
		try {
			const answer = await board(boardUrl(siteId, EVERYTHING));
			if (mine === previewGeneration) preview = answer.departures;
		} catch (e) {
			if (mine === previewGeneration) error = e;
		}
	}

	function choose(site: Site): void {
		if (!form.name || form.name === form.site?.name) form.name = site.name;
		form.site = { id: site.id, name: site.name };
		form.transport = '';
		form.lines = [];
		form.direction = null;
		loadPreview(site.id);
	}

	function toggle(line: number, on: boolean): void {
		form.lines = on ? [...form.lines, line].sort((a, b) => a - b) : form.lines.filter((l) => l !== line);
	}

	async function submit(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		if (!form.site) return;
		saving = true;
		try {
			error = null;
			await onsubmit({
				name: form.name.trim(),
				site_id: form.site.id,
				site_name: form.site.name,
				transport: form.transport || null,
				lines: form.lines,
				direction: form.direction
			});
		} catch (e) {
			error = e;
		} finally {
			saving = false;
		}
	}

	onMount(() => {
		if (opened) loadPreview(opened.site_id);
		return () => clearTimeout(searchTimer);
	});
</script>

<form class="form" onsubmit={submit}>
	<ErrorNotice {error} />
	{#if opened}
		<p>Site: {opened.site_name}</p>
	{:else}
		<label
			>Find a station or stop
			<input type="search" bind:value={query} oninput={queueSearch} maxlength="100" />
		</label>
		{#if found !== null}
			{#if !found.length}
				<p class="muted">SL has no site called “{query.trim()}”.</p>
			{:else}
				<fieldset>
					<legend>Site</legend>
					{#each found as site (site.id)}
						<label class="choice"
							><input
								type="radio"
								name="site"
								required
								checked={form.site?.id === site.id}
								onchange={() => choose(site)}
							/>
							{site.name}</label
						>
					{/each}
				</fieldset>
			{/if}
		{/if}
	{/if}

	{#if form.site}
		<label>Name <input bind:value={form.name} maxlength="100" required /></label>
		{#if preview === null}
			<p class="muted">Loading the lines at {form.site.name}…</p>
		{/if}
		<label
			>Mode
			<select bind:value={form.transport}>
				<option value="">Every mode</option>
				{#each modes as mode (mode)}
					<option value={mode}>{MODES[mode] ?? mode}</option>
				{/each}
			</select>
		</label>
		{#if lines.length}
			<fieldset>
				<legend>Lines <span class="muted">(none ticked: every line)</span></legend>
				<div class="lines">
					{#each lines as [line, designation] (line)}
						<label class="choice"
							><input
								type="checkbox"
								checked={form.lines.includes(line)}
								onchange={(e) => toggle(line, e.currentTarget.checked)}
							/>
							{designation}</label
						>
					{/each}
				</div>
			</fieldset>
		{/if}
		<fieldset>
			<legend>Direction</legend>
			<label class="choice"
				><input
					type="radio"
					name="direction"
					checked={form.direction === null}
					onchange={() => (form.direction = null)}
				/>
				Both</label
			>
			{#each [1, 2] as const as code (code)}
				<label class="choice"
					><input
						type="radio"
						name="direction"
						checked={form.direction === code}
						onchange={() => (form.direction = code)}
					/>
					{#if towards.get(code)?.length}
						Towards {towards.get(code)!.join(', ')}
					{:else}
						Direction {code}
					{/if}</label
				>
			{/each}
		</fieldset>
	{/if}

	<div class="actions">
		<button type="submit" class="primary" disabled={!form.site || saving}>{submitLabel}</button>
		{#if oncancel}<button type="button" onclick={oncancel}>Cancel</button>{/if}
	</div>
</form>

<style>
	.form {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.form > label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	fieldset {
		border: 1px solid var(--hc-border);
		border-radius: 0.5rem;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.choice {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}
	.lines {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(6rem, 1fr));
		gap: 0.25rem;
	}
</style>
