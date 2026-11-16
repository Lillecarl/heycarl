<script lang="ts">
	import { onMount, type Snippet } from 'svelte';
	import { registry } from '#lib/plugins.svelte.ts';
	import ThemePicker from '#lib/ThemePicker.svelte';
	// The default base16 palettes, light and dark (nix/themes.nix).
	import '../themes/defaults.css';
	import '../app.css';

	let { children }: { children: Snippet } = $props();

	onMount(() => {
		registry.load();
	});

	async function signOut(): Promise<void> {
		await fetch('/auth/logout', { method: 'POST' });
		location.href = '/';
	}
</script>

<nav>
	<a href="/" class="home">Hey Carl</a>
	{#each registry.pages as page (page.tag)}
		<a href="/p/{page.plugin}/{page.tag}">{page.title}</a>
	{/each}
	<a href="/history">History</a>
	<a href="/connect">Connect AI</a>
	<ThemePicker />
	<button class="sign-out" onclick={signOut}>Sign out</button>
</nav>

{#if registry.error}
	<p class="error">{registry.error}</p>
{/if}

<main>
	{@render children()}
</main>
