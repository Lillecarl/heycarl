<script lang="ts">
	import { onMount } from 'svelte';

	type Grant = {
		id: number;
		client_name: string | null;
		redirect_hosts: string[];
		scopes: string[];
		created_at: string;
	};

	const url = `${location.origin}/mcp`;
	let copied = $state(false);
	let grants = $state<Grant[] | null>(null);
	let error = $state<string | null>(null);

	async function load(): Promise<void> {
		const r = await fetch('/api/oauth/grants');
		if (!r.ok) {
			error = `Could not load connections: ${r.status}`;
			return;
		}
		grants = await r.json();
	}

	async function copy(): Promise<void> {
		await navigator.clipboard.writeText(url);
		copied = true;
		setTimeout(() => (copied = false), 2000);
	}

	async function revoke(grant: Grant): Promise<void> {
		const name = grant.client_name ?? grant.redirect_hosts.join(', ');
		if (!confirm(`Disconnect ${name}? It loses access at once.`)) return;
		const r = await fetch(`/api/oauth/grants/${grant.id}`, { method: 'DELETE' });
		if (!r.ok && r.status !== 404) {
			error = `Could not disconnect: ${r.status}`;
			return;
		}
		await load();
	}

	onMount(load);
</script>

<h1>Connect an AI</h1>
<p>
	Hey Carl is an MCP server. An AI assistant that supports remote MCP can read your contacts,
	reminders and todos, and change them if you allow it. You approve each connection here, as
	yourself, and choose what it may do.
</p>

<div class="url">
	<code>{url}</code>
	<button onclick={copy}>{copied ? 'Copied' : 'Copy'}</button>
</div>

<details open>
	<summary>Claude (claude.ai and the Claude apps)</summary>
	<ol>
		<li>Open <strong>Settings → Connectors</strong>.</li>
		<li>Select <strong>Add custom connector</strong>.</li>
		<li>Give it the name <em>Hey Carl</em> and paste the URL above. Leave the advanced OAuth fields empty.</li>
		<li>Select <strong>Add</strong>, then <strong>Connect</strong>. Sign in, and approve on the Hey Carl page.</li>
	</ol>
	<p class="note">
		On a Team or Enterprise plan, an owner adds the connector under Organization settings → Connectors.
		A connector added on the web also works in the desktop and mobile apps.
	</p>
</details>

<details>
	<summary>ChatGPT</summary>
	<ol>
		<li>Open <strong>Settings → Apps &amp; Connectors → Advanced settings</strong> and turn on <strong>Developer mode</strong>.</li>
		<li>Back in <strong>Apps &amp; Connectors</strong>, select <strong>Create</strong>.</li>
		<li>Paste the URL above and choose <strong>OAuth</strong> as the authentication.</li>
		<li>Sign in, and approve on the Hey Carl page.</li>
		<li>In a chat, turn on the connector from the <strong>+</strong> menu, or mention it by name.</li>
	</ol>
	<p class="note">Needs a paid plan. OpenAI moves these settings now and then; look under Security if Developer mode is not under Connectors.</p>
</details>

<details>
	<summary>Claude Code</summary>
	<pre>claude mcp add --transport http heycarl {url}</pre>
	<p>Then run <code>/mcp</code> in a session, select <em>heycarl</em> and authenticate. A browser opens to sign in.</p>
</details>

<details>
	<summary>Other clients</summary>
	<p>
		Any client that speaks MCP's Streamable HTTP transport with OAuth works. It finds everything
		from the URL: the server answers 401 and points at its metadata, and clients register
		themselves (dynamic client registration). The scopes are <code>read</code> and <code>write</code>.
	</p>
</details>

<h2>Connected apps</h2>
{#if error}
	<p class="error">{error}</p>
{/if}
{#if grants === null}
	<p class="note">Loading…</p>
{:else if grants.length === 0}
	<p class="note">Nothing is connected.</p>
{:else}
	<ul class="grants">
		{#each grants as grant (grant.id)}
			<li>
				<div>
					<strong>{grant.client_name ?? 'Unnamed app'}</strong>
					<span class="note">via {grant.redirect_hosts.join(', ')}</span>
					<div class="note">
						{grant.scopes.includes('write') ? 'Read and change' : 'Read only'} · since
						{new Date(grant.created_at).toLocaleDateString()}
					</div>
				</div>
				<button onclick={() => revoke(grant)}>Disconnect</button>
			</li>
		{/each}
	</ul>
{/if}

<style>
	.url {
		display: flex;
		gap: 0.5rem;
		align-items: center;
		margin: 1rem 0;
		padding: 0.5rem 0.75rem;
		border: 1px solid var(--hc-muted);
		border-radius: 6px;
	}
	.url code {
		flex: 1;
		overflow-wrap: anywhere;
	}
	details {
		margin: 0.75rem 0;
		padding: 0.5rem 0.75rem;
		border: 1px solid var(--hc-muted);
		border-radius: 6px;
	}
	summary {
		cursor: pointer;
		font-weight: 600;
	}
	pre {
		overflow-x: auto;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	.note {
		color: var(--hc-muted);
	}
	.grants {
		list-style: none;
		padding: 0;
	}
	.grants li {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 1rem;
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--hc-muted);
	}
	button {
		font: inherit;
		padding: 0.3rem 0.8rem;
		color: var(--hc-fg);
		background: none;
		border: 1px solid var(--hc-muted);
		border-radius: 6px;
		cursor: pointer;
	}
	.error {
		color: var(--hc-error);
	}
</style>
