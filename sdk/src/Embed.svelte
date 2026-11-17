<!--
	One entry, shown by its own plugin inside another plugin's page:
	<Embed scope="departures" table="stop" key={{ id: 3 }} />
	The plugin declares the element in its manifest (`frontend.embeds`).
	Renders nothing when there is none, so the host keeps its plain link.
-->
<script lang="ts">
	import { embedTag } from './index.ts';

	let { scope, table, key }: { scope: string; table: string; key: Record<string, unknown> } =
		$props();

	let tag = $derived(embedTag(scope, table));

	// A property, not an attribute: an attribute holds only a string.
	const withKey = (element: Element) => {
		(element as Element & { key: Record<string, unknown> }).key = key;
	};
</script>

{#await tag then found}
	{#if found}
		<svelte:element this={found} {@attach withKey} />
	{/if}
{/await}
