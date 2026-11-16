<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { registry } from '#lib/plugins.svelte.ts';

	// Old plugin names, as kernel/heycarl/migrations.py RENAMES. The kernel
	// redirects a full load; this covers a link followed inside the app. Not
	// a redirect in +page.ts: load may not read url.hash, and the hash holds
	// the entry (#/contact/12).
	const RENAMED: Record<string, string> = { crm: 'prm' };

	const found = $derived(
		registry.pages.find((p) => p.plugin === page.params.plugin && p.tag === page.params.page)
	);

	$effect(() => {
		const old = page.params.plugin ?? '';
		const renamed = RENAMED[old];
		if (!renamed) return;
		const tag = (page.params.page ?? '').replace(`heycarl-${old}`, `heycarl-${renamed}`);
		goto(`/p/${renamed}/${tag}${page.url.search}${page.url.hash}`, { replaceState: true });
	});
</script>

<svelte:head>
	<title>{found ? `${found.title} · Hey Carl` : 'Hey Carl'}</title>
</svelte:head>

{#if found}
	<h1>{found.title}</h1>
	{#key found.tag}
		<svelte:element this={found.tag} />
	{/key}
{:else if registry.ready && !RENAMED[page.params.plugin ?? '']}
	<h1>Not found</h1>
	<p>No such page.</p>
{/if}

<style>
	h1 {
		margin: 0 0 1rem;
		font-size: 1.5rem;
	}
</style>
