<script lang="ts">
	import { onMount, type Snippet } from 'svelte';
	import { registry } from '#lib/plugins.svelte.ts';
	import '../app.css';

	let { children }: { children: Snippet } = $props();

	onMount(() => {
		registry.load();
	});
</script>

<nav>
	<a href="/" class="home">Hey Carl</a>
	{#each registry.pages as page (page.tag)}
		<a href="/p/{page.plugin}/{page.tag}">{page.title}</a>
	{/each}
</nav>

{#if registry.error}
	<p class="error">{registry.error}</p>
{/if}

<main>
	{@render children()}
</main>
