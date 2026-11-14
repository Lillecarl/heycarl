<script lang="ts">
	import { page } from '$app/state';
	import { registry } from '#lib/plugins.svelte.ts';

	const found = $derived(
		registry.pages.find((p) => p.plugin === page.params.plugin && p.tag === page.params.page)
	);
</script>

{#if found}
	{#key found.tag}
		<svelte:element this={found.tag} />
	{/key}
{:else if registry.ready}
	<p>No such page.</p>
{/if}
