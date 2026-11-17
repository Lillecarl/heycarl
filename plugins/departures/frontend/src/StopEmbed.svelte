<svelte:options
	customElement={{
		tag: 'heycarl-departures-stop',
		extend: withBase,
		props: { key: { type: 'Object' } }
	}}
/>

<!--
	One stop's next departures inside another plugin's page (the manifest's
	`embeds`). A stop that is gone shows nothing: the host still has its link.
-->
<script lang="ts">
	import { untrack } from 'svelte';
	import { data } from '@heycarl/sdk';
	import ErrorNotice from '@heycarl/sdk/ErrorNotice.svelte';
	import { withBase } from '@heycarl/sdk/base';
	import Board from './Board.svelte';
	import { SCOPE, type Stop } from './stops.ts';

	let { key }: { key: { id?: unknown } | undefined } = $props();

	let stop = $state<Stop | null>(null);
	let error = $state<unknown>(null);

	let generation = 0;
	async function load(id: number): Promise<void> {
		const mine = ++generation;
		try {
			const [row] = await data<Stop[]>(SCOPE, `stop?id=eq.${id}`);
			if (mine === generation) stop = row ?? null;
		} catch (e) {
			if (mine === generation) error = e;
		}
	}

	$effect(() => {
		const id = Number(key?.id);
		untrack(() => {
			stop = null;
			if (Number.isSafeInteger(id)) load(id);
		});
	});
</script>

<ErrorNotice {error} />
{#if stop}
	<Board siteId={stop.site_id} filter={stop} limit={4} />
{/if}
