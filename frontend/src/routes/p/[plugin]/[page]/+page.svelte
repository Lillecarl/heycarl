<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { registry } from '#lib/plugins.svelte.ts';

	// Old plugin names, as kernel/heycarl/migrations.py RENAMES. The kernel
	// redirects a full load; this covers a link followed inside the app.
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

{#if found}
	{#key found.tag}
		<svelte:element this={found.tag} />
	{/key}
{:else if registry.ready && !RENAMED[page.params.plugin ?? '']}
	<p>No such page.</p>
{/if}
