<script lang="ts">
	import { onMount } from 'svelte';
	import { api, data, HttpError, json } from '@heycarl/sdk';

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

	// PostgREST and the kernel answer an error as JSON with a message.
	function reason(e: unknown): string {
		if (!(e instanceof HttpError)) return String(e);
		try {
			const body: unknown = JSON.parse(e.body);
			if (body && typeof body === 'object' && 'message' in body) return String(body.message);
		} catch {
			// Not JSON: the status says enough.
		}
		return `HTTP ${e.status}`;
	}

	async function attempt(what: string, work: () => Promise<void>): Promise<void> {
		error = null;
		try {
			await work();
		} catch (e) {
			error = `${what}: ${reason(e)}`;
		}
	}

	async function load(): Promise<void> {
		grants = await api<Grant[]>('/api/oauth/grants');
	}

	async function copy(): Promise<void> {
		await navigator.clipboard.writeText(url);
		copied = true;
		setTimeout(() => (copied = false), 2000);
	}

	const grantName = (g: Grant) => g.client_name ?? g.redirect_hosts.join(', ');

	// The grant whose Disconnect waits for a second press.
	let revoking = $state<number | null>(null);

	function revoke(grant: Grant): Promise<void> {
		revoking = null;
		return attempt('Could not disconnect', async () => {
			try {
				await api(`/api/oauth/grants/${grant.id}`, { method: 'DELETE' });
			} catch (e) {
				// Already gone is what was asked for.
				if (!(e instanceof HttpError && e.status === 404)) throw e;
			}
			await load();
		});
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
	let passwords = $state<AppPassword[] | null>(null);
	let made = $state<Made | null>(null);
	let deviceName = $state('');
	let allowWrite = $state(false);
	// The app password whose Revoke waits for a second press.
	let revokingPassword = $state<string | null>(null);

	async function loadPasswords(): Promise<void> {
		passwords = await data<AppPassword[]>('core', 'app_password?order=created_at');
	}

	function makePassword(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		return attempt('Could not make an app password', async () => {
			made = await data<Made>(
				'core',
				'rpc/app_password_create',
				json('POST', { name: deviceName, allow_write: allowWrite })
			);
			deviceName = '';
			allowWrite = false;
			await loadPasswords();
		});
	}

	function revokePassword(p: AppPassword): Promise<void> {
		revokingPassword = null;
		return attempt('Could not revoke', async () => {
			await data('core', `app_password?id=eq.${encodeURIComponent(p.id)}`, { method: 'DELETE' });
			if (made?.username === p.id) made = null;
			await loadPasswords();
		});
	}

	onMount(() => {
		attempt('Could not load connections', load);
		attempt('Could not load app passwords', loadPasswords);
	});
</script>

<svelte:head><title>Connect AI · Hey Carl</title></svelte:head>

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
	<p class="error" role="alert">{error}</p>
{/if}
{#if grants === null}
	<p class="note">Loading…</p>
{:else if grants.length === 0}
	<p class="note">Nothing is connected.</p>
{:else}
	<ul class="plain grants">
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
				<div class="actions">
					{#if revoking === grant.id}
						<button class="danger" onclick={() => revoke(grant)}
							>Disconnect {grantName(grant)}: it loses access at once</button
						>
						<button onclick={() => (revoking = null)}>Keep</button>
					{:else}
						<button aria-label="Disconnect {grantName(grant)}" onclick={() => (revoking = grant.id)}
							>Disconnect</button
						>
					{/if}
				</div>
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
	<input
		bind:value={deviceName}
		aria-label="Device"
		placeholder="Device, e.g. Phone"
		maxlength="100"
		required
	/>
	<label class="check"><input type="checkbox" bind:checked={allowWrite} /> Allow changes</label>
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
	<ul class="plain grants">
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
				<div class="actions">
					{#if revokingPassword === p.id}
						<button class="danger" onclick={() => revokePassword(p)}
							>Revoke {p.name}: it stops working at once</button
						>
						<button onclick={() => (revokingPassword = null)}>Keep</button>
					{:else}
						<button aria-label="Revoke {p.name}" onclick={() => (revokingPassword = p.id)}
							>Revoke</button
						>
					{/if}
				</div>
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
	.check {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		min-height: 3rem;
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
	.grants li {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		align-items: center;
		gap: 0.5rem 1rem;
		padding: 0.5rem 0;
		border-bottom: 1px solid var(--hc-border);
		overflow-wrap: anywhere;
	}
</style>
