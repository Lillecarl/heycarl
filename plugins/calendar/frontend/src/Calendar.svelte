<svelte:options customElement={{ tag: 'heycarl-calendar', extend: withBase }} />

<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { api, data, json } from '@heycarl/sdk';
	import { withBase } from '@heycarl/sdk/base';
	import { hashHref, hashId } from '@heycarl/sdk/route';
	import Related from '@heycarl/sdk/Related.svelte';

	interface Account {
		google_account: string;
		connected_at: string;
		last_sync_at: string | null;
		last_error: string | null;
		needs_reconnect: boolean;
	}
	interface Cal {
		id: number;
		name: string;
		color: string;
		is_primary: boolean;
		selected: boolean;
	}
	interface Ev {
		id: number;
		calendar: string;
		color: string;
		title: string;
		description: string;
		location: string;
		all_day: boolean;
		starts_at: string;
		ends_at: string;
		start_date: string | null;
		end_date: string | null;
		attendees: string[];
		html_link: string;
	}
	interface Person {
		contact_id: number;
		first_name: string;
		last_name: string;
		email: string;
	}

	const SCOPE = 'calendar';
	const PLUGIN = '/api/plugins/calendar';
	const DAYS = 30;

	let configured = $state<boolean | null>(null);
	let account = $state<Account | null>(null);
	let calendars = $state<Cal[]>([]);
	// null until the first answer, so "Nothing in the next days" never shows early.
	let events = $state<Ev[] | null>(null);
	let error = $state<string | null>(null);
	let busy = $state(false);
	let leaving = $state(false);
	let open = $derived(hashId('event'));
	let detail = $state<Ev | null>(null);
	let people = $state<Person[]>([]);

	let days = $derived(group(events ?? []));

	// An all-day event belongs to its date; a timed one to the local day it starts.
	function dayOf(e: Ev): string {
		if (e.all_day && e.start_date) return e.start_date;
		return new Intl.DateTimeFormat('sv-SE').format(new Date(e.starts_at));
	}

	function group(list: Ev[]): [string, Ev[]][] {
		const out = new Map<string, Ev[]>();
		for (const e of list) {
			const d = dayOf(e);
			out.set(d, [...(out.get(d) ?? []), e]);
		}
		return [...out.entries()];
	}

	async function load(): Promise<void> {
		const status = await api<{ configured: boolean }>(`${PLUGIN}/status`);
		configured = status.configured;
		const [a, c] = await Promise.all([
			data<Account[]>(SCOPE, 'account'),
			data<Cal[]>(SCOPE, 'calendar?order=is_primary.desc,name')
		]);
		account = a[0] ?? null;
		calendars = c;
		const now = new Date();
		const until = new Date(now.getTime() + DAYS * 86_400_000);
		events = await data<Ev[]>(
			SCOPE,
			`agenda?ends_at=gt.${now.toISOString()}&starts_at=lt.${until.toISOString()}` +
				'&order=starts_at,id&limit=500'
		);
	}

	async function loadDetail(id: number): Promise<void> {
		const [rows, p] = await Promise.all([
			data<Ev[]>(SCOPE, `agenda?id=eq.${id}`),
			data<Person[]>(SCOPE, `rpc/event_people?event_id=${id}`)
		]);
		if (open === id) {
			detail = rows[0] ?? null;
			people = p;
		}
	}

	async function run(action: () => Promise<unknown>): Promise<void> {
		busy = true;
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
		busy = false;
	}

	async function connect(): Promise<void> {
		try {
			const { url } = await api<{ url: string }>(`${PLUGIN}/connect`, { method: 'POST' });
			location.href = url;
		} catch (e) {
			error = String(e);
		}
	}

	function syncNow(): void {
		run(async () => {
			const r = await api<{ ok: boolean; error: string | null }>(`${PLUGIN}/sync`, {
				method: 'POST'
			});
			if (!r.ok && r.error) throw new Error(r.error);
		});
	}

	function disconnect(): void {
		leaving = false;
		run(async () => {
			const r = await api<{ revoked: boolean }>(`${PLUGIN}/disconnect`, { method: 'POST' });
			if (!r.revoked)
				throw new Error(
					'Disconnected here, but Google did not confirm. Remove Hey Carl in your Google account.'
				);
		});
	}

	function choose(c: Cal, selected: boolean): void {
		run(() => api(`${PLUGIN}/calendars/${c.id}`, json('POST', { selected })));
	}

	// The hash names the open event; its detail loads when it changes.
	$effect(() => {
		const id = open;
		untrack(() => {
			detail = null;
			people = [];
			if (id !== null) loadDetail(id).catch((e) => (error = String(e)));
		});
	});

	const dayTitle = (d: string) =>
		new Date(d + 'T12:00:00').toLocaleDateString(undefined, {
			weekday: 'long',
			day: 'numeric',
			month: 'long'
		});
	const time = (at: string) =>
		new Date(at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
	const stamp = (at: string) =>
		new Date(at).toLocaleString(undefined, {
			day: 'numeric',
			month: 'short',
			hour: '2-digit',
			minute: '2-digit'
		});

	onMount(() => {
		load().catch((e) => (error = String(e)));
	});
</script>

{#if error}<p class="error" role="alert">{error}</p>{/if}

{#if configured === false}
	<p class="muted">
		Google Calendar is not configured on this Hey Carl. The person who runs it sets a Google client
		in the NixOS module.
	</p>
{:else if configured && !account}
	<section class="connect">
		<p>Bring your Google calendars into Hey Carl. Hey Carl reads them and never changes them.</p>
		<button class="primary" onclick={connect}>Connect Google Calendar</button>
	</section>
{:else if account}
	<section class="status">
		<p>
			<strong>{account.google_account || 'Google'}</strong>
			<span class="muted">
				{#if account.last_sync_at}synced {stamp(account.last_sync_at)}{:else}not synced yet{/if}
			</span>
		</p>
		{#if account.needs_reconnect}
			<p class="error">Google no longer accepts this connection.</p>
			<button class="primary" onclick={connect}>Connect again</button>
		{:else if account.last_error}
			<p class="error">The last sync failed: {account.last_error}</p>
		{/if}
		<div class="actions">
			<button disabled={busy} onclick={syncNow}>Sync now</button>
			{#if leaving}
				<button class="danger" onclick={disconnect}>Disconnect Google Calendar</button>
				<button onclick={() => (leaving = false)}>Keep</button>
			{:else}
				<button onclick={() => (leaving = true)}>Disconnect</button>
			{/if}
		</div>
		<details>
			<summary>Calendars</summary>
			<ul class="plain calendars">
				{#each calendars as c (c.id)}
					<li>
						<label>
							<input
								type="checkbox"
								checked={c.selected}
								disabled={busy}
								onchange={(e) => choose(c, e.currentTarget.checked)}
							/>
							<span class="dot" style:background={c.color || 'var(--hc-muted)'}></span>
							{c.name}
						</label>
					</li>
				{/each}
			</ul>
		</details>
	</section>

	{#if events === null}
		<p class="muted">Loading…</p>
	{:else if days.length}
		{#each days as [day, list] (day)}
			<h2>{dayTitle(day)}</h2>
			<ul class="plain events">
				{#each list as e (e.id)}
					<li>
						<a
							class="row"
							href={open === e.id ? hashHref() : hashHref('event', e.id)}
							class:open={open === e.id}
							aria-expanded={open === e.id}
						>
							<span class="dot" style:background={e.color || 'var(--hc-muted)'}></span>
							<span class="when muted">{e.all_day ? 'All day' : time(e.starts_at)}</span>
							<span class="title">{e.title || '(no title)'}</span>
						</a>
						{#if open === e.id && detail}
							<div class="detail">
								<p class="muted">
									{e.calendar}{#if !e.all_day}, {time(e.starts_at)}–{time(e.ends_at)}{/if}
								</p>
								{#if detail.location}<p>{detail.location}</p>{/if}
								{#if detail.description}<p class="description">{detail.description}</p>{/if}
								{#if people.length}
									<p>
										With
										{#each people as p, i (p.contact_id)}{i ? ', ' : ''}<a
												href="/p/prm/heycarl-prm#/contact/{p.contact_id}"
												>{p.first_name} {p.last_name}</a
											>{/each}
									</p>
								{/if}
								{#if detail.attendees.length}
									<p class="muted">{detail.attendees.join(', ')}</p>
								{/if}
								{#if detail.html_link.startsWith('https://')}
									<a href={detail.html_link} target="_blank" rel="noopener noreferrer"
										>Open in Google Calendar</a
									>
								{/if}
								<Related scope={SCOPE} table="event" key={{ id: e.id }} />
							</div>
						{/if}
					</li>
				{/each}
			</ul>
		{/each}
	{:else}
		<p class="muted">Nothing in the next {DAYS} days.</p>
	{/if}
{/if}

<style>
	.status,
	.connect {
		display: grid;
		gap: 0.5rem;
		margin-bottom: 1rem;
	}
	.calendars li label {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		min-height: 3rem;
	}
	h2 {
		margin: 1rem 0 0.25rem;
		font-size: 1rem;
	}
	.events a.row {
		display: grid;
		grid-template-columns: 0.75rem 5.5rem 1fr;
		gap: 0.5rem;
		align-items: center;
		min-height: 3rem;
		color: inherit;
		text-decoration: none;
		border-bottom: 1px solid var(--hc-border);
	}
	.events a.row.open {
		font-weight: bold;
	}
	.dot {
		display: inline-block;
		width: 0.75rem;
		height: 0.75rem;
		border-radius: 50%;
	}
	.when {
		white-space: nowrap;
		font-variant-numeric: tabular-nums;
	}
	.title {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.detail {
		display: grid;
		gap: 0.5rem;
		padding: 0.5rem 0 1rem 1.25rem;
		overflow-wrap: anywhere;
	}
	.detail .description {
		white-space: pre-wrap;
	}
</style>
