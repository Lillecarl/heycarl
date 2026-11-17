<svelte:options customElement={{ tag: 'heycarl-google-contacts', extend: withBase }} />

<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { api, data, json, type GoogleAccount } from '@heycarl/sdk';
	import ErrorNotice from '@heycarl/sdk/ErrorNotice.svelte';
	import GoogleAccounts from '@heycarl/sdk/GoogleAccounts.svelte';
	import { withBase } from '@heycarl/sdk/base';
	import { hashHref, hashId } from '@heycarl/sdk/route';
	import Related from '@heycarl/sdk/Related.svelte';
	import {
		BOOK_SELECT,
		REASONS,
		bookName,
		compare,
		entryName,
		type Book,
		type Entry,
		type Suggestion
	} from './fields.ts';

	interface Status {
		id: number;
		last_sync_at: string | null;
		last_error: string | null;
	}
	interface Found {
		id: number;
		first_name: string;
		last_name: string;
	}

	const SCOPE = 'google_contacts';
	const PLUGIN = '/api/plugins/google_contacts';
	const PEOPLE = '/p/prm/heycarl-prm';
	const SHOWN = ['suggested', 'unlinked', 'linked', 'all'] as const;
	const SHOWN_TITLES: Record<(typeof SHOWN)[number], string> = {
		suggested: 'Suggested links',
		unlinked: 'Not linked',
		linked: 'Linked',
		all: 'All'
	};

	let accounts = $state<GoogleAccount[] | null>(null);
	let statuses = $state<Status[]>([]);
	// null until the first answer, so the empty text never shows early.
	let entries = $state<Entry[] | null>(null);
	let suggestions = $state<Suggestion[]>([]);
	let shown = $state<(typeof SHOWN)[number]>('suggested');
	let filter = $state('');
	let error = $state<unknown>(null);
	let busy = $state(false);
	let open = $derived(hashId('person'));
	let book = $state<Book | null>(null);
	let query = $state('');
	let found = $state<Found[]>([]);
	let searches = 0;

	const statusOf = (id: number) => statuses.find((s) => s.id === id);
	const suggestionsOf = (id: number) => suggestions.filter((s) => s.person_id === id);
	const openEntry = $derived(entries?.find((e) => e.id === open) ?? null);
	const rows = $derived(openEntry && book ? compare(openEntry, book) : []);
	const listed = $derived(
		(entries ?? []).filter((e) => {
			const text = filter.trim().toLowerCase();
			if (text && !entryName(e).toLowerCase().includes(text)) return false;
			if (shown === 'suggested') return e.contact_id === null && suggestionsOf(e.id).length > 0;
			if (shown === 'unlinked') return e.contact_id === null;
			if (shown === 'linked') return e.contact_id !== null;
			return true;
		})
	);
	const stamp = (at: string) =>
		new Date(at).toLocaleString(undefined, {
			day: 'numeric',
			month: 'short',
			hour: '2-digit',
			minute: '2-digit'
		});

	async function load(): Promise<void> {
		[statuses, entries, suggestions] = await Promise.all([
			data<Status[]>(SCOPE, 'account?select=id,last_sync_at,last_error'),
			data<Entry[]>(SCOPE, 'entry?order=display_name,id'),
			data<Suggestion[]>(SCOPE, 'rpc/suggestions')
		]);
	}

	async function loadBook(contactId: number | null): Promise<void> {
		book = null;
		if (contactId === null) return;
		const [found] = await data<Book[]>('prm', `contact?id=eq.${contactId}&select=${BOOK_SELECT}`);
		book = found ?? null;
	}

	async function run(action: () => Promise<unknown>): Promise<void> {
		busy = true;
		try {
			error = null;
			await action();
			await load();
			await loadBook(openEntry?.contact_id ?? null);
		} catch (e) {
			error = e;
		}
		busy = false;
	}

	function syncNow(): void {
		run(async () => {
			const r = await api<{ ok: boolean; error: string | null }>(`${PLUGIN}/sync`, {
				method: 'POST'
			});
			if (!r.ok && r.error) throw new Error(r.error);
		});
	}

	const link = (personId: number, contactId: number) =>
		run(() => data(SCOPE, 'match', json('POST', { person_id: personId, contact_id: contactId })));

	const unlink = (personId: number) =>
		run(() => data(SCOPE, `match?person_id=eq.${personId}`, { method: 'DELETE' }));

	const copy = (personId: number, fields: string[]) =>
		run(() => data(SCOPE, 'rpc/copy_fields', json('POST', { person_id: personId, fields })));

	const importPerson = (personId: number) =>
		run(() => data(SCOPE, 'rpc/import_person', json('POST', { person_id: personId })));

	let timer: ReturnType<typeof setTimeout> | undefined;
	function search(text: string): void {
		query = text;
		clearTimeout(timer);
		const mine = ++searches;
		if (!text.trim()) {
			found = [];
			return;
		}
		timer = setTimeout(() => {
			data<Found[]>('prm', `rpc/search?q=${encodeURIComponent(text.trim())}&limit=10`).then(
				(rows) => {
					if (mine === searches) found = rows;
				},
				(e: unknown) => (error = e)
			);
		}, 250);
	}

	// The hash names the open Google contact; its book contact loads with it.
	$effect(() => {
		const contactId = openEntry?.contact_id ?? null;
		untrack(() => {
			query = '';
			found = [];
			loadBook(contactId).catch((e: unknown) => (error = e));
		});
	});

	onMount(() => {
		load().catch((e: unknown) => (error = e));
	});
</script>

<ErrorNotice {error} />

<!-- Hidden, not removed, under an open contact: it keeps `accounts` loaded. -->
<section class="accounts" hidden={open !== null}>
	{#if accounts?.length === 0}
		<p>
			Bring your Google contacts into Hey Carl, and link them to the people in your contact book.
			Hey Carl reads Google and never changes it. Links stay in Hey Carl.
		</p>
	{/if}
	<GoogleAccounts plugin={SCOPE} bind:accounts onchange={() => run(async () => {})}>
		{#snippet detail(a, granted)}
			{@const status = statusOf(a.id)}
			{#if granted}
				<p class="muted">
					{#if status?.last_sync_at}synced {stamp(status.last_sync_at)}{:else}not synced yet{/if}
				</p>
				{#if status?.last_error && !a.needs_reconnect}
					<p class="error">The last sync failed: {status.last_error}</p>
				{/if}
			{/if}
		{/snippet}
	</GoogleAccounts>
	{#if accounts?.length}
		<div class="actions">
			<button disabled={busy} onclick={syncNow}>Sync now</button>
		</div>
	{/if}
</section>

{#if open !== null && entries !== null && !openEntry}
	<p>No such Google contact. <a href={hashHref()}>All Google contacts</a></p>
{:else if openEntry}
	{@const e = openEntry}
	<p><a href={hashHref()}>All Google contacts</a></p>
	<h2>{entryName(e)}</h2>
	<p class="muted">From {e.google_account}</p>

	{#if e.contact_id !== null}
		<p>
			Linked to <a href="{PEOPLE}#/contact/{e.contact_id}"
				>{bookName(e.contact_first_name, e.contact_last_name)}</a
			>
		</p>
		<div class="actions">
			<button disabled={busy} onclick={() => unlink(e.id)}>Unlink</button>
		</div>
		{#if book}
			<h3>Fields</h3>
			<ul class="plain compare">
				{#each rows.filter((r) => r.google.length || r.book.length) as r (r.field)}
					<li>
						<div class="field">
							<strong>{r.title}</strong>
							{#if r.differs}
								<button
									disabled={busy}
									aria-label="Copy {r.title} from Google"
									onclick={() => copy(e.id, [r.field])}>Copy</button
								>
							{/if}
						</div>
						<dl>
							<dt>Google</dt>
							<dd>
								{#each r.google as v, i (i)}<div>{v}</div>{:else}<span class="muted">none</span
									>{/each}
							</dd>
							<dt>Hey Carl</dt>
							<dd>
								{#each r.book as v, i (i)}<div>{v}</div>{:else}<span class="muted">none</span
									>{/each}
							</dd>
						</dl>
					</li>
				{/each}
			</ul>
			{#if rows.some((r) => r.differs)}
				<div class="actions">
					<button
						class="primary"
						disabled={busy}
						onclick={() =>
							copy(
								e.id,
								rows.filter((r) => r.differs).map((r) => r.field)
							)}>Copy every field from Google</button
					>
				</div>
			{:else}
				<p class="muted">Hey Carl has everything this Google contact has.</p>
			{/if}
		{/if}
	{:else}
		<h3>Link to a contact</h3>
		{#if suggestionsOf(e.id).length}
			<ul class="plain choices">
				{#each suggestionsOf(e.id) as s (s.contact_id)}
					<li>
						<button disabled={busy} onclick={() => link(e.id, s.contact_id)}
							>Link to {bookName(s.first_name, s.last_name)}</button
						>
						<span class="muted">{REASONS[s.reason]}</span>
					</li>
				{/each}
			</ul>
		{/if}
		<label class="search">
			<span>Find a contact in People</span>
			<input type="search" value={query} oninput={(ev) => search(ev.currentTarget.value)} />
		</label>
		{#if found.length}
			<ul class="plain choices">
				{#each found as c (c.id)}
					<li>
						<button disabled={busy} onclick={() => link(e.id, c.id)}
							>Link to {bookName(c.first_name, c.last_name)}</button
						>
					</li>
				{/each}
			</ul>
		{/if}
		<div class="actions">
			<button disabled={busy} onclick={() => importPerson(e.id)}>Add to People as new</button>
		</div>
	{/if}
	<Related scope={SCOPE} table="person" key={{ id: e.id }} />
{:else if accounts?.length}
	<fieldset class="shown">
		<legend>Show</legend>
		{#each SHOWN as s (s)}
			<label><input type="radio" name="shown" value={s} bind:group={shown} /> {SHOWN_TITLES[s]}</label>
		{/each}
	</fieldset>
	<label class="search">
		<span>Filter by name</span>
		<input type="search" bind:value={filter} />
	</label>
	{#if entries === null}
		<p class="muted">Loading…</p>
	{:else if listed.length}
		<ul class="plain list">
			{#each listed as e (e.id)}
				{@const best = e.contact_id === null ? suggestionsOf(e.id)[0] : undefined}
				<li>
					<a href={hashHref('person', e.id)}>
						<span class="name">{entryName(e)}</span>
						<span class="muted">
							{#if e.contact_id !== null}
								linked to {bookName(e.contact_first_name, e.contact_last_name)}
							{:else if best}
								{REASONS[best.reason]} as {bookName(best.first_name, best.last_name)}
							{:else}
								{e.emails[0]?.value ?? e.google_account}
							{/if}
						</span>
					</a>
					{#if best}
						<button
							disabled={busy}
							aria-label="Link {entryName(e)} to {bookName(best.first_name, best.last_name)}"
							onclick={() => link(e.id, best.contact_id)}>Link</button
						>
					{/if}
				</li>
			{/each}
		</ul>
	{:else}
		<p class="muted">No Google contacts here.</p>
	{/if}
{/if}

<style>
	.accounts:not([hidden]) {
		display: grid;
		gap: 0.5rem;
		margin-bottom: 1rem;
	}
	.accounts p {
		margin: 0;
	}
	.shown {
		display: flex;
		flex-wrap: wrap;
		gap: 0 1rem;
		border: none;
		padding: 0;
		margin: 0 0 0.5rem;
	}
	.shown label,
	.search {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
		min-height: 3rem;
	}
	.search input {
		flex: 1 1 12rem;
		min-width: 0;
	}
	.list li {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		border-bottom: 1px solid var(--hc-border);
	}
	.list a {
		flex: 1;
		min-width: 0;
		display: grid;
		min-height: 3rem;
		align-content: center;
		padding: 0.25rem 0;
		color: inherit;
		text-decoration: none;
		overflow-wrap: anywhere;
	}
	.name {
		font-weight: bold;
	}
	.choices li {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
		margin-bottom: 0.5rem;
	}
	.compare li {
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--hc-border);
	}
	.field {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.5rem;
		min-height: 3rem;
	}
	.compare dl {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 0.25rem 1rem;
		margin: 0;
	}
	.compare dt {
		color: var(--hc-muted);
	}
	.compare dd {
		margin: 0;
		min-width: 0;
		overflow-wrap: anywhere;
	}
</style>
