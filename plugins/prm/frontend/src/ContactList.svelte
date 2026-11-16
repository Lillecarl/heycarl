<script lang="ts">
	import { onMount } from 'svelte';
	import { data, json } from '@heycarl/sdk';
	import Avatar from './Avatar.svelte';
	import {
		SCOPE,
		type Contact,
		type ImportantDate,
		contactHref,
		fullName,
		inDays,
		localDate,
		today
	} from './types.ts';

	const COLUMNS = 'id,first_name,last_name,nickname,company,favorite,archived,last_contacted';

	// null until the first answer.
	let contacts = $state<Contact[] | null>(null);
	let upcoming = $state<ImportantDate[]>([]);
	let overdue = $state<Contact[]>([]);
	let query = $state('');
	let archived = $state(false);
	let first = $state('');
	let last = $state('');
	let error = $state<string | null>(null);

	// Only the newest search may fill the list.
	let generation = 0;

	async function loadContacts(): Promise<void> {
		const mine = ++generation;
		const q = query.trim();
		// PostgREST filters an RPC's result like a table.
		const path = q
			? `rpc/search?q=${encodeURIComponent(q)}&select=${COLUMNS}`
			: `contact?select=${COLUMNS}&order=favorite.desc,first_name,last_name`;
		const rows = await data<Contact[]>(SCOPE, `${path}&archived=is.${archived}`);
		if (mine === generation) contacts = rows;
	}

	async function loadDashboard(): Promise<void> {
		[upcoming, overdue] = await Promise.all([
			data<ImportantDate[]>(
				SCOPE,
				'important_date?select=id,kind,label,year,next_occurrence,days_until,turns,' +
					'contact(id,first_name,last_name)&days_until=lte.30&order=days_until'
			),
			data<Contact[]>(
				SCOPE,
				`contact?select=id,first_name,last_name,touch_due&archived=is.false` +
					`&touch_due=lte.${today()}&order=touch_due`
			)
		]);
	}

	function report(e: unknown): void {
		error = String(e);
	}

	let searchTimer: ReturnType<typeof setTimeout> | undefined;
	function onSearch(): void {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => loadContacts().catch(report), 200);
	}

	async function add(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		const first_name = first.trim();
		if (!first_name) return;
		try {
			const [created] = await data<Contact[]>(
				SCOPE,
				'contact',
				json('POST', { first_name, last_name: last.trim() }, 'return=representation')
			);
			location.hash = contactHref(created.id);
		} catch (e) {
			report(e);
		}
	}

	const short = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short' });

	function describe(d: ImportantDate): string {
		const what = d.kind === 'other' ? d.label || 'date' : d.kind;
		const count =
			d.turns === null || d.kind === 'memorial' || d.kind === 'other'
				? ''
				: d.kind === 'birthday'
					? `, turns ${d.turns}`
					: `, ${d.turns} years`;
		return `${what}${count}`;
	}

	onMount(() => {
		window.scrollTo(0, 0);
		loadContacts().catch(report);
		loadDashboard().catch(report);
		return () => clearTimeout(searchTimer);
	});
</script>

{#if error}<p class="error" role="alert">{error}</p>{/if}

{#if !query && (upcoming.length || overdue.length)}
	<section>
		{#if upcoming.length}
			<h2>Coming up</h2>
			<ul class="plain">
				{#each upcoming as d (d.id)}
					<li>
						<span class="grow">
							<a href={contactHref(d.contact!.id)}>{fullName(d.contact!)}</a>
							<span class="muted">{describe(d)}</span>
						</span>
						<span class="muted">
							{inDays(d.days_until)} · {short.format(localDate(d.next_occurrence))}
						</span>
					</li>
				{/each}
			</ul>
		{/if}
		{#if overdue.length}
			<h2>Time to get in touch</h2>
			<ul class="plain">
				{#each overdue as c (c.id)}
					<li>
						<a class="grow" href={contactHref(c.id)}>{fullName(c)}</a>
						<span class="muted">since {short.format(localDate(c.touch_due!))}</span>
					</li>
				{/each}
			</ul>
		{/if}
	</section>
{/if}

<section>
	<h2 class="visually-hidden">Everyone</h2>
	<search class="row">
		<input
			class="grow"
			type="search"
			aria-label="Search people"
			placeholder="Search names, emails, phones, tags…"
			bind:value={query}
			oninput={onSearch}
		/>
		<label class="check muted">
			<input type="checkbox" bind:checked={archived} onchange={() => loadContacts().catch(report)} />
			Archived
		</label>
	</search>

	{#if contacts === null}
		<p class="muted">Loading…</p>
	{:else}
		<ul class="plain">
			{#each contacts as c (c.id)}
				<li>
					<Avatar contact={c} />
					<a class="grow" href={contactHref(c.id)}>
						{fullName(c)}
						{#if c.nickname}<span class="muted">“{c.nickname}”</span>{/if}
						{#if c.company}<span class="muted">· {c.company}</span>{/if}
						{#if c.favorite}<span aria-hidden="true">★</span><span class="visually-hidden"
								>, favourite</span
							>{/if}
					</a>
				</li>
			{:else}
				<li class="muted">{query ? 'Nobody matches.' : 'No contacts yet.'}</li>
			{/each}
		</ul>
	{/if}
</section>

<section>
	<h2>Add someone</h2>
	<form class="row" onsubmit={add}>
		<input
			class="grow"
			aria-label="First name"
			placeholder="First name"
			bind:value={first}
			maxlength="100"
			required
		/>
		<input
			class="grow"
			aria-label="Last name"
			placeholder="Last name"
			bind:value={last}
			maxlength="100"
		/>
		<button type="submit">Add</button>
	</form>
</section>

<style>
	section {
		margin: 1.5rem 0;
	}
	h2 {
		font-size: 1rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--hc-muted);
		margin: 0 0 0.5rem;
	}
	ul + h2 {
		margin-top: 1.5rem;
	}
	.row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
		margin: 0.5rem 0;
	}
	.row .grow {
		flex: 1 1 10rem;
	}
	.check {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		min-height: 3rem;
	}
	li {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		min-height: 3rem;
	}
	.grow {
		flex: 1;
		min-width: 0;
	}
	a {
		text-decoration: none;
	}
</style>
