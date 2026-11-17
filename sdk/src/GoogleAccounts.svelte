<!--
	The person's Google accounts, for a plugin page that reads Google
	(DESIGN.md "Google accounts"): connect another, renew, grant this
	plugin's scopes, disconnect. The kernel owns the accounts, so every
	plugin shows the same list.
	<GoogleAccounts plugin="calendar" bind:accounts onchange={load}>
		{#snippet detail(account, granted)}…{/snippet}
	</GoogleAccounts>
-->
<script lang="ts">
	import { onMount, type Snippet } from 'svelte';
	import ErrorNotice from './ErrorNotice.svelte';
	import {
		connectGoogle,
		disconnectGoogle,
		googleAccounts,
		googleStatus,
		type GoogleAccount
	} from './index.ts';

	let {
		plugin,
		accounts = $bindable(null),
		detail,
		onchange
	}: {
		plugin: string;
		// null until loaded.
		accounts?: GoogleAccount[] | null;
		detail?: Snippet<[GoogleAccount, boolean]>;
		onchange?: () => void;
	} = $props();

	let configured = $state<boolean | null>(null);
	let needs = $state<string[]>([]);
	let leaving = $state<number | null>(null);
	let busy = $state(false);
	let error = $state<unknown>(null);

	const granted = (a: GoogleAccount) => needs.every((s) => a.scopes.includes(s));
	const here = () => location.pathname + location.search + location.hash;

	async function load(): Promise<void> {
		const status = await googleStatus();
		configured = status.configured;
		needs = status.plugins[plugin] ?? [];
		accounts = configured ? await googleAccounts() : [];
	}

	async function connect(accountId?: number): Promise<void> {
		try {
			error = null;
			await connectGoogle(here(), accountId);
		} catch (e) {
			error = e;
		}
	}

	async function disconnect(a: GoogleAccount): Promise<void> {
		busy = true;
		leaving = null;
		try {
			error = null;
			const r = await disconnectGoogle(a.id);
			if (!r.revoked)
				error = new Error(
					`Disconnected ${a.email} here, but Google did not confirm. Remove Hey Carl in that Google account.`
				);
			await load();
			onchange?.();
		} catch (e) {
			error = e;
		}
		busy = false;
	}

	onMount(() => {
		load().catch((e: unknown) => (error = e));
	});
</script>

<ErrorNotice {error} />

{#if configured === false}
	<p class="muted">
		Google is not configured on this Hey Carl. The person who runs it sets a Google client in the
		NixOS module.
	</p>
{:else if configured && accounts}
	<ul class="plain accounts">
		{#each accounts as a (a.id)}
			<li>
				<p><strong>{a.email || 'Google account'}</strong></p>
				{#if a.needs_reconnect}
					<p class="error">Google no longer accepts this connection.</p>
					<div class="actions">
						<button class="primary" onclick={() => connect(a.id)}>Connect {a.email} again</button>
					</div>
				{:else if !granted(a)}
					<p class="muted">This account has not given this page access.</p>
					<div class="actions">
						<button class="primary" onclick={() => connect(a.id)}>Grant access</button>
					</div>
				{/if}
				{@render detail?.(a, granted(a))}
				<div class="actions">
					{#if leaving === a.id}
						<button class="danger" disabled={busy} onclick={() => disconnect(a)}
							>Disconnect {a.email} from every page</button
						>
						<button onclick={() => (leaving = null)}>Keep</button>
					{:else}
						<button disabled={busy} onclick={() => (leaving = a.id)}>Disconnect {a.email}</button>
					{/if}
				</div>
			</li>
		{/each}
	</ul>
	<div class="actions">
		<button class:primary={accounts.length === 0} onclick={() => connect()}>
			{accounts.length ? 'Connect another Google account' : 'Connect a Google account'}
		</button>
	</div>
{/if}

<style>
	.accounts li {
		display: grid;
		gap: 0.5rem;
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--hc-border);
	}
	.accounts p {
		margin: 0;
		overflow-wrap: anywhere;
	}
</style>
