<script lang="ts">
	import { onMount, type Snippet } from 'svelte';
	import { afterNavigate } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '@heycarl/sdk';
	import ErrorNotice from '@heycarl/sdk/ErrorNotice.svelte';
	import { registry } from '#lib/plugins.svelte.ts';
	import ThemePicker from '#lib/ThemePicker.svelte';
	// The default base16 palettes, light and dark (nix/themes.nix).
	import '../themes/defaults.css';
	import '../app.css';
	import '@heycarl/sdk/base.css';

	let { children }: { children: Snippet } = $props();

	// The narrow-screen menu. A button with aria-expanded, not <details>: on a
	// wide screen the list shows whatever the button's state, and a closed
	// <details> hides its content in every layout.
	let open = $state(false);
	let menuButton = $state<HTMLButtonElement>();

	const here = (href: string) => (page.url.pathname === href ? 'page' : undefined);

	afterNavigate(() => {
		open = false;
	});

	function closeOnEscape(event: KeyboardEvent): void {
		if (open && event.key === 'Escape') {
			open = false;
			menuButton?.focus();
		}
	}

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

<svelte:window onkeydown={closeOnEscape} />

<a class="skip" href="#main">Skip to content</a>

<nav aria-label="Hey Carl">
	<div class="bar">
		<a href="/" class="home" aria-current={here('/')}>Hey Carl</a>
		<button
			class="menu-button"
			aria-expanded={open}
			aria-controls="menu"
			bind:this={menuButton}
			onclick={() => (open = !open)}><span aria-hidden="true">☰</span> Menu</button
		>
	</div>
	<ul id="menu" class="plain menu" class:open>
		{#each registry.pages as p (p.tag)}
			{@const href = `/p/${p.plugin}/${p.tag}`}
			<li><a {href} aria-current={here(href)}>{p.title}</a></li>
		{/each}
		<li><a href="/history" aria-current={here('/history')}>History</a></li>
		<li><a href="/connect" aria-current={here('/connect')}>Connect AI</a></li>
		<li class="end"><ThemePicker /></li>
		<li><button class="sign-out" onclick={signOut}>Sign out</button></li>
	</ul>
</nav>

<div class="problem"><ErrorNotice error={registry.error} /></div>

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
		padding: 0 1rem;
		padding-top: env(safe-area-inset-top);
		background: var(--hc-surface);
		border-bottom: 1px solid var(--hc-border);
	}
	.bar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
	}
	nav a {
		display: flex;
		align-items: center;
		min-height: 3rem;
		text-decoration: none;
	}
	nav a[aria-current='page'] {
		text-decoration: underline;
		text-underline-offset: 0.3em;
	}
	.home {
		font-weight: bold;
		color: var(--hc-fg);
	}
	.menu {
		display: none;
		flex-direction: column;
		padding-bottom: 0.5rem;
	}
	.menu.open {
		display: flex;
	}
	.menu li {
		border-top: 1px solid var(--hc-border);
	}
	.menu .end {
		padding: 0.5rem 0;
	}
	.sign-out {
		width: 100%;
		padding-inline: 0;
		text-align: start;
		color: var(--hc-muted);
		border-color: transparent;
	}
	/* Wide enough for every link on one row today; more plugins wrap. */
	@media (min-width: 90rem) {
		nav {
			display: flex;
			align-items: flex-start;
			gap: 1rem;
		}
		.menu-button {
			display: none;
		}
		.menu,
		.menu.open {
			display: flex;
			flex: 1;
			flex-direction: row;
			flex-wrap: wrap;
			align-items: center;
			gap: 0 1rem;
			padding-bottom: 0;
		}
		.menu li {
			border-top: none;
		}
		.sign-out {
			width: auto;
			padding-inline: revert-layer;
		}
		.menu .end {
			margin-inline-start: auto;
			padding: 0;
		}
	}
	.problem {
		padding: 0 1rem;
	}
	main {
		padding: 1rem;
	}
	main:focus {
		outline: none;
	}
</style>
