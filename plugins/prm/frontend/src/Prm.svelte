<svelte:options customElement={{ tag: 'heycarl-prm', extend: withBase }} />

<script lang="ts">
	import { onMount } from 'svelte';
	import { data, json } from '@heycarl/sdk';
	import { withBase } from '@heycarl/sdk/base';
	import { hashId } from '@heycarl/sdk/route';
	import ContactList from './ContactList.svelte';
	import ContactView from './ContactView.svelte';
	import { SCOPE } from './types.ts';

	// The view lives in the hash (#/contact/12), so a reminder's link opens
	// the contact and the shell's router sees one page.
	// Each view scrolls to the top when it mounts.
	let open = $derived(hashId('contact'));

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
	.prm {
		max-width: 48rem;
		margin: 0 auto;
	}
</style>
