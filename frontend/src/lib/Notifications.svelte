<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '@heycarl/sdk';
	import ErrorNotice from '@heycarl/sdk/ErrorNotice.svelte';

	type Status = 'unsupported' | 'denied' | 'off' | 'on' | 'working';

	let status = $state<Status>('working');
	let message = $state<string | null>(null);
	let error = $state<unknown>(null);

	function urlBase64ToBytes(base64: string): Uint8Array<ArrayBuffer> {
		const padded = (base64 + '='.repeat((4 - (base64.length % 4)) % 4))
			.replace(/-/g, '+')
			.replace(/_/g, '/');
		return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
	}

	async function current(): Promise<PushSubscription | null> {
		const registration = await navigator.serviceWorker.ready;
		return registration.pushManager.getSubscription();
	}

	async function refresh(): Promise<void> {
		if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
			status = 'unsupported';
			return;
		}
		if (Notification.permission === 'denied') {
			status = 'denied';
			return;
		}
		status = (await current()) ? 'on' : 'off';
	}

	async function enable(): Promise<void> {
		status = 'working';
		message = null;
		error = null;
		try {
			if ((await Notification.requestPermission()) !== 'granted') {
				status = 'denied';
				return;
			}
			const { key } = await api<{ key: string }>('/api/push/key');
			const registration = await navigator.serviceWorker.ready;
			// Subscribe again on every enable: the server key may have changed.
			await (await registration.pushManager.getSubscription())?.unsubscribe();
			const subscription = await registration.pushManager.subscribe({
				userVisibleOnly: true,
				applicationServerKey: urlBase64ToBytes(key)
			});
			await api('/api/push/subscribe', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify(subscription.toJSON())
			});
			status = 'on';
		} catch (e) {
			error = new Error('Notifications were not turned on', { cause: e });
			await refresh();
		}
	}

	async function test(): Promise<void> {
		message = null;
		error = null;
		try {
			const { delivered } = await api<{ delivered: number }>('/api/push/test', { method: 'POST' });
			message = `Sent to ${delivered} device(s).`;
		} catch (e) {
			error = new Error('The test was not sent', { cause: e });
		}
	}

	onMount(() => {
		refresh().catch((e: unknown) => (error = e));
	});
</script>

<section>
	<h2>Notifications</h2>
	{#if status === 'unsupported'}
		<p>This browser cannot receive push notifications. On Android, install the app first.</p>
	{:else if status === 'denied'}
		<p>Notifications are blocked for this site. Allow them in the browser's site settings.</p>
	{:else if status === 'off'}
		<button onclick={enable}>Enable on this device</button>
	{:else if status === 'on'}
		<p>On for this device.</p>
		<button onclick={test}>Send a test</button>
		<button onclick={enable}>Subscribe again</button>
	{:else}
		<p class="muted">Checking…</p>
	{/if}
	{#if message}<p class="muted" role="status">{message}</p>{/if}
	<ErrorNotice {error} />
</section>

<style>
	section {
		margin-top: 2rem;
	}
</style>
