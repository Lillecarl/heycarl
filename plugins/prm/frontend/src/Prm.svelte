<svelte:options customElement="heycarl-prm" />

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
		syncZone().catch((e) => console.warn('prm: time zone not saved', e));
		const onHash = (): void => {
			open = parse(location.hash);
			window.scrollTo(0, 0);
		};
		addEventListener('hashchange', onHash);
		return () => removeEventListener('hashchange', onHash);
	});
</script>

<div class="prm">
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
	.prm {
		max-width: 48rem;
		margin: 0 auto;
	}
	.prm :global(a) {
		color: var(--hc-accent, teal);
		text-decoration: none;
	}
	.prm :global(input),
	.prm :global(select),
	.prm :global(textarea),
	.prm :global(button) {
		font: inherit;
		color: inherit;
		background: transparent;
		border: 1px solid var(--hc-muted, gray);
		border-radius: 0.25rem;
		padding: 0.4rem 0.5rem;
	}
	.prm :global(button) {
		cursor: pointer;
	}
	.prm :global(textarea) {
		width: 100%;
		box-sizing: border-box;
		min-height: 4rem;
	}
	.prm :global(form.row) {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin: 0.5rem 0;
	}
	.prm :global(form.row .grow) {
		flex: 1 1 10rem;
		min-width: 0;
	}
	.prm :global(.row),
	.prm :global(.row-head) {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.75rem;
	}
	.prm :global(h1) {
		font-size: 1.5rem;
		margin: 0;
	}
	.prm :global(section) {
		margin: 1.5rem 0;
	}
	.prm :global(h2) {
		font-size: 1rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--hc-muted, gray);
		margin: 0 0 0.5rem;
	}
	.prm :global(ul + h2) {
		margin-top: 1.5rem;
	}
	.prm :global(dd input:not([type='number'])) {
		width: 100%;
		box-sizing: border-box;
	}
	.prm :global(ul.plain) {
		list-style: none;
		padding: 0;
		margin: 0;
	}
	.prm :global(ul.plain > li) {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		padding: 0.4rem 0;
	}
	.prm :global(.grow) {
		flex: 1;
		min-width: 0;
	}
	.prm :global(.muted) {
		color: var(--hc-muted, gray);
		font-size: 0.9em;
	}
	.prm :global(.error) {
		color: var(--hc-error, red);
	}
	.prm :global(.icon) {
		border: none;
		background: none;
		padding: 0 0.25rem;
		font-size: 1.2rem;
		line-height: 1;
	}
	.prm :global(.avatar) {
		flex: none;
		display: grid;
		place-items: center;
		width: 2.25rem;
		height: 2.25rem;
		border-radius: 50%;
		font-weight: 600;
		color: var(--hc-base00);
	}
	.prm :global(.avatar.big) {
		width: 4rem;
		height: 4rem;
		font-size: 1.5rem;
	}
	.prm :global(.chip) {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		border: 1px solid var(--hc-muted, gray);
		border-radius: 1rem;
		padding: 0.1rem 0.6rem;
		margin: 0 0.25rem 0.25rem 0;
		font-size: 0.9em;
	}
	.prm :global(.timeline li) {
		align-items: flex-start;
		border-left: 2px solid var(--hc-muted, gray);
		padding-left: 0.75rem;
	}
	.prm :global(.pre) {
		white-space: pre-wrap;
		margin: 0.25rem 0 0;
	}
	.prm :global(dl) {
		display: grid;
		grid-template-columns: max-content 1fr;
		gap: 0.5rem 1rem;
		align-items: center;
	}
	.prm :global(dt) {
		color: var(--hc-muted, gray);
	}
	.prm :global(dd) {
		margin: 0;
	}
</style>
