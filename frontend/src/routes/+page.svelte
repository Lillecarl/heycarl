<script lang="ts">
	import { registry } from '#lib/plugins.svelte.ts';
	import Notifications from '#lib/Notifications.svelte';
</script>

<h1>Hey Carl</h1>
{#if registry.ready}
	<ul>
		{#each registry.pages as page (page.tag)}
			<li><a href="/p/{page.plugin}/{page.tag}">{page.title}</a></li>
		{:else}
			<li>No plugin has a page.</li>
		{/each}
	</ul>
{/if}

<Notifications />
