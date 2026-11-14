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

	let contacts = $state<Contact[]>([]);
	let upcoming = $state<ImportantDate[]>([]);
	let overdue = $state<Contact[]>([]);
	let query = $state('');
	let archived = $state(false);
	let first = $state('');
	let last = $state('');
	let error = $state<string | null>(null);

	async function loadContacts(): Promise<void> {
		const q = query.trim();
		// PostgREST filters an RPC's result like a table.
		const path = q
			? `rpc/search?q=${encodeURIComponent(q)}&select=${COLUMNS}`
			: `contact?select=${COLUMNS}&order=favorite.desc,first_name,last_name`;
		contacts = await data<Contact[]>(SCOPE, `${path}&archived=is.${archived}`);
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
		const count = d.turns !== null && d.kind !== 'memorial' ? ` (${d.turns})` : '';
		return `${what}${count}`;
	}

	onMount(() => {
		loadContacts().catch(report);
		loadDashboard().catch(report);
	});
</script>

{#if error}<p class="error">{error}</p>{/if}

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
	<form class="row" onsubmit={(e) => e.preventDefault()}>
		<input
			class="grow"
			type="search"
			placeholder="Search names, emails, phones, tags…"
			bind:value={query}
			oninput={onSearch}
		/>
		<label class="muted">
			<input type="checkbox" bind:checked={archived} onchange={() => loadContacts().catch(report)} />
			Archived
		</label>
	</form>

	<ul class="plain">
		{#each contacts as c (c.id)}
			<li>
				<Avatar contact={c} />
				<a class="grow" href={contactHref(c.id)}>
					{fullName(c)}
					{#if c.nickname}<span class="muted">“{c.nickname}”</span>{/if}
					{#if c.company}<span class="muted">· {c.company}</span>{/if}
				</a>
				{#if c.favorite}<span aria-label="Favourite">★</span>{/if}
			</li>
		{:else}
			<li class="muted">{query ? 'Nobody matches.' : 'No contacts yet.'}</li>
		{/each}
	</ul>
</section>

<section>
	<h2>Add someone</h2>
	<form class="row" onsubmit={add}>
		<input class="grow" placeholder="First name" bind:value={first} maxlength="100" required />
		<input class="grow" placeholder="Last name" bind:value={last} maxlength="100" />
		<button type="submit">Add</button>
	</form>
</section>
