<svelte:options customElement="heycarl-crm" />

<script lang="ts">
	import { onMount } from 'svelte';
	import { data, json } from '@heycarl/sdk';
	import ContactList from './ContactList.svelte';
	import ContactView from './ContactView.svelte';
	import { SCOPE } from './types.ts';

	// The view lives in the hash (#/contact/12), so a reminder's link opens
	// the contact and the shell's router sees one page.
	function parse(hash: string): number | null {
		const m = /^#\/contact\/(\d+)$/.exec(hash);
		return m ? Number(m[1]) : null;
	}

	let open = $state(parse(location.hash));

	// Dates and reminder times follow this zone, on the server.
	async function syncZone(): Promise<void> {
		const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
		const rows = await data<{ timezone: string }[]>(SCOPE, 'preference?select=timezone');
		if (rows[0]?.timezone === timezone) return;
		await data(
			SCOPE,
			'preference?on_conflict=owner_sub',
			json('POST', { timezone }, 'resolution=merge-duplicates')
		);
	}

	onMount(() => {
		syncZone().catch((e) => console.warn('crm: time zone not saved', e));
		const onHash = (): void => {
			open = parse(location.hash);
			window.scrollTo(0, 0);
		};
		addEventListener('hashchange', onHash);
		return () => removeEventListener('hashchange', onHash);
	});
</script>

<div class="crm">
	{#if open === null}
		<ContactList />
	{:else}
		{#key open}
			<ContactView id={open} />
		{/key}
	{/if}
</div>

<style>
	/* Every component's styles live here: the children render inside this
	   element's shadow root, and :global reaches them there. */
	.crm {
		max-width: 48rem;
		margin: 0 auto;
	}
	.crm :global(a) {
		color: var(--hc-accent, teal);
		text-decoration: none;
	}
	.crm :global(input),
	.crm :global(select),
	.crm :global(textarea),
	.crm :global(button) {
		font: inherit;
		color: inherit;
		background: transparent;
		border: 1px solid var(--hc-muted, gray);
		border-radius: 0.25rem;
		padding: 0.4rem 0.5rem;
	}
	.crm :global(button) {
		cursor: pointer;
	}
	.crm :global(textarea) {
		width: 100%;
		box-sizing: border-box;
		min-height: 4rem;
	}
	.crm :global(form.row) {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin: 0.5rem 0;
	}
	.crm :global(form.row .grow) {
		flex: 1 1 10rem;
		min-width: 0;
	}
	.crm :global(.row),
	.crm :global(.row-head) {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.75rem;
	}
	.crm :global(h1) {
		font-size: 1.5rem;
		margin: 0;
	}
	.crm :global(section) {
		margin: 1.5rem 0;
	}
	.crm :global(h2) {
		font-size: 1rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--hc-muted, gray);
		margin: 0 0 0.5rem;
	}
	.crm :global(ul.plain) {
		list-style: none;
		padding: 0;
		margin: 0;
	}
	.crm :global(ul.plain > li) {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		padding: 0.4rem 0;
	}
	.crm :global(.grow) {
		flex: 1;
		min-width: 0;
	}
	.crm :global(.muted) {
		color: var(--hc-muted, gray);
		font-size: 0.9em;
	}
	.crm :global(.error) {
		color: var(--hc-error, red);
	}
	.crm :global(.icon) {
		border: none;
		background: none;
		padding: 0 0.25rem;
		font-size: 1.2rem;
		line-height: 1;
	}
	.crm :global(.avatar) {
		flex: none;
		display: grid;
		place-items: center;
		width: 2.25rem;
		height: 2.25rem;
		border-radius: 50%;
		font-weight: 600;
		color: #1d2021;
	}
	.crm :global(.avatar.big) {
		width: 4rem;
		height: 4rem;
		font-size: 1.5rem;
	}
	.crm :global(.chip) {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		border: 1px solid var(--hc-muted, gray);
		border-radius: 1rem;
		padding: 0.1rem 0.6rem;
		margin: 0 0.25rem 0.25rem 0;
		font-size: 0.9em;
	}
	.crm :global(.timeline li) {
		align-items: flex-start;
		border-left: 2px solid var(--hc-muted, gray);
		padding-left: 0.75rem;
	}
	.crm :global(.pre) {
		white-space: pre-wrap;
		margin: 0.25rem 0 0;
	}
	.crm :global(dl) {
		display: grid;
		grid-template-columns: max-content 1fr;
		gap: 0.5rem 1rem;
		align-items: center;
	}
	.crm :global(dt) {
		color: var(--hc-muted, gray);
	}
	.crm :global(dd) {
		margin: 0;
	}
</style>
