/// <reference types="@sveltejs/kit" />
/// <reference no-default-lib="true"/>
/// <reference lib="esnext" />
/// <reference lib="webworker" />

// SvelteKit builds and registers this file. It caches nothing: every page and
// API call goes through the gate, and a cached shell would outlive a session.
// Its jobs are installability and push.

const sw = self as unknown as ServiceWorkerGlobalScope;

interface PushPayload {
	title: string;
	body?: string;
	// Route to open when the notification is tapped.
	url?: string;
	tag?: string;
}

sw.addEventListener('install', () => {
	sw.skipWaiting();
});

sw.addEventListener('activate', (event) => {
	event.waitUntil(sw.clients.claim());
});

sw.addEventListener('push', (event) => {
	const payload: PushPayload = event.data?.json() ?? { title: 'Hey Carl' };
	event.waitUntil(
		sw.registration.showNotification(payload.title, {
			body: payload.body,
			tag: payload.tag,
			icon: '/icons/icon-192.png',
			badge: '/icons/icon-192.png',
			data: { url: payload.url ?? '/' }
		})
	);
});

sw.addEventListener('notificationclick', (event) => {
	event.notification.close();
	const url: string = event.notification.data?.url ?? '/';
	event.waitUntil(
		(async () => {
			const windows = await sw.clients.matchAll({ type: 'window', includeUncontrolled: true });
			for (const client of windows) {
				if ('focus' in client) {
					await client.navigate(url);
					return client.focus();
				}
			}
			return sw.clients.openWindow(url);
		})()
	);
});
