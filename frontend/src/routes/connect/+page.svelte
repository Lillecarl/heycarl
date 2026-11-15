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

	type AppPassword = {
		id: string;
		name: string;
		scopes: string[];
		created_at: string;
		last_used_at: string | null;
		expires_at: string;
	};
	type Made = { username: string; secret: string };

	const davUrl = `${location.origin}/dav/`;
	const passwordsApi = '/api/data/core/app_password';
	let passwords = $state<AppPassword[] | null>(null);
	let made = $state<Made | null>(null);
	let deviceName = $state('');
	let allowWrite = $state(false);

	async function loadPasswords(): Promise<void> {
		const r = await fetch(`${passwordsApi}?order=created_at`);
		if (!r.ok) {
			error = `Could not load app passwords: ${r.status}`;
			return;
		}
		passwords = await r.json();
	}

	async function makePassword(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		const r = await fetch('/api/data/core/rpc/app_password_create', {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ name: deviceName, allow_write: allowWrite })
		});
		if (!r.ok) {
			error = `Could not make an app password: ${(await r.json()).message ?? r.status}`;
			return;
		}
		made = await r.json();
		deviceName = '';
		allowWrite = false;
		await loadPasswords();
	}

	async function revokePassword(p: AppPassword): Promise<void> {
		if (!confirm(`Revoke the app password for ${p.name}? It stops working at once.`)) return;
		const r = await fetch(`${passwordsApi}?id=eq.${encodeURIComponent(p.id)}`, {
			method: 'DELETE'
		});
		if (!r.ok) {
			error = `Could not revoke: ${r.status}`;
			return;
		}
		if (made?.username === p.id) made = null;
		await loadPasswords();
	}

	onMount(() => {
		load();
		loadPasswords();
	});
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
	<summary>OpenCode</summary>
	<p>Add the server to <code>~/.config/opencode/opencode.json</code>, or to a project's <code>opencode.json</code>:</p>
	<pre>{JSON.stringify({ mcp: { heycarl: { type: 'remote', url, enabled: true } } }, null, 2)}</pre>
	<p>Then sign in, and approve on the Hey Carl page:</p>
	<pre>opencode mcp auth heycarl</pre>
	<p class="note"><code>opencode mcp list</code> shows whether it is connected; <code>opencode mcp debug heycarl</code> shows each sign-in step.</p>
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

<h2>App passwords</h2>
<p>
	Calendar and contact apps (CalDAV and CardDAV) cannot sign in here. Make one app password for each
	device. It works only for those apps, never for this site or for MCP.
</p>

<form onsubmit={makePassword}>
	<input bind:value={deviceName} placeholder="Device, e.g. Phone" maxlength="100" required />
	<label><input type="checkbox" bind:checked={allowWrite} /> Allow changes</label>
	<button type="submit">Make</button>
</form>

{#if made}
	<div class="made">
		<p><strong>Copy these now. The password does not show again.</strong></p>
		<dl>
			<dt>Server</dt>
			<dd><code>{davUrl}</code></dd>
			<dt>Username</dt>
			<dd><code>{made.username}</code></dd>
			<dt>Password</dt>
			<dd><code>{made.secret}</code></dd>
		</dl>
		<button onclick={() => (made = null)}>Done</button>
	</div>
{/if}

{#if passwords === null}
	<p class="note">Loading…</p>
{:else if passwords.length === 0}
	<p class="note">No app passwords.</p>
{:else}
	<ul class="grants">
		{#each passwords as p (p.id)}
			<li>
				<div>
					<strong>{p.name}</strong>
					<span class="note"><code>{p.id}</code></span>
					<div class="note">
						{p.scopes.includes('write') ? 'Read and change' : 'Read only'} · last used
						{p.last_used_at ? new Date(p.last_used_at).toLocaleDateString() : 'never'} · ends
						{new Date(p.expires_at).toLocaleDateString()}
					</div>
				</div>
				<button onclick={() => revokePassword(p)}>Revoke</button>
			</li>
		{/each}
	</ul>
{/if}

<style>
	form {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		align-items: center;
		margin: 1rem 0;
	}
	input:not([type]) {
		font: inherit;
		padding: 0.3rem 0.5rem;
		color: var(--hc-fg);
		background: none;
		border: 1px solid var(--hc-muted);
		border-radius: 6px;
	}
	.made {
		margin: 1rem 0;
		padding: 0.5rem 0.75rem;
		border: 1px solid var(--hc-fg);
		border-radius: 6px;
	}
	.made dl {
		display: grid;
		grid-template-columns: max-content 1fr;
		gap: 0.25rem 1rem;
	}
	.made dd {
		margin: 0;
		overflow-wrap: anywhere;
	}
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
