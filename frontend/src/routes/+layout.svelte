<script lang="ts">
	import { onMount, type Snippet } from 'svelte';
	import { page } from '$app/state';
	import { api } from '@heycarl/sdk';
	import { registry } from '#lib/plugins.svelte.ts';
	import ThemePicker from '#lib/ThemePicker.svelte';
	// The default base16 palettes, light and dark (nix/themes.nix).
	import '../themes/defaults.css';
	import '../app.css';
	import '@heycarl/sdk/base.css';

	let { children }: { children: Snippet } = $props();

	const here = (href: string) => (page.url.pathname === href ? 'page' : undefined);

	onMount(() => {
		registry.load();
	});

	// Not a form POST: the answer redirects on to the identity provider, and
	// Chrome checks form-action 'self' on that redirect too.
	async function signOut(): Promise<void> {
		await api('/auth/logout', { method: 'POST' });
		location.href = '/';
	}
</script>

<a class="skip" href="#main">Skip to content</a>

<nav aria-label="Hey Carl">
	<a href="/" class="home" aria-current={here('/')}>Hey Carl</a>
	{#each registry.pages as p (p.tag)}
		{@const href = `/p/${p.plugin}/${p.tag}`}
		<a {href} aria-current={here(href)}>{p.title}</a>
	{/each}
	<a href="/history" aria-current={here('/history')}>History</a>
	<a href="/connect" aria-current={here('/connect')}>Connect AI</a>
	<ThemePicker />
	<button class="sign-out" onclick={signOut}>Sign out</button>
</nav>

{#if registry.error}
	<p class="error" role="alert">{registry.error}</p>
{/if}

<main id="main" tabindex="-1">
	{@render children()}
</main>

<style>
	.skip {
		position: absolute;
		left: 1rem;
		top: -3rem;
		padding: 0.5rem 1rem;
		background: var(--hc-surface);
		z-index: 1;
	}
	.skip:focus {
		top: 0.5rem;
	}
	nav {
		display: flex;
		align-items: center;
		gap: 1rem;
		padding: 0 1rem;
		padding-top: env(safe-area-inset-top);
		background: var(--hc-surface);
		border-bottom: 1px solid var(--hc-border);
		overflow-x: auto;
	}
	nav a {
		display: flex;
		align-items: center;
		min-height: 3rem;
		text-decoration: none;
		white-space: nowrap;
	}
	nav a[aria-current='page'] {
		text-decoration: underline;
		text-underline-offset: 0.3em;
	}
	.home {
		font-weight: bold;
		color: var(--hc-fg);
	}
	.sign-out {
		flex: none;
		color: var(--hc-muted);
		border-color: transparent;
	}
	.error {
		padding: 0 1rem;
	}
	main {
		padding: 1rem;
	}
	main:focus {
		outline: none;
	}
</style>
