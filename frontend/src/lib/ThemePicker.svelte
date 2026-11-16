<script lang="ts">
	import {
		loadThemes,
		savedTheme,
		saveTheme,
		storedTheme,
		useTheme,
		type Theme
	} from '#lib/theme.ts';

	const DEFAULT = '';

	let themes: Theme[] = $state.raw([]);
	let chosen = $state(storedTheme() ?? DEFAULT);
	let problem: string | null = $state(null);

	const light = $derived(themes.filter((t) => t.variant === 'light'));
	const dark = $derived(themes.filter((t) => t.variant === 'dark'));

	$effect(() => {
		// Without the saved row, this browser's copy stands.
		const saved = savedTheme().catch((error: Error) => {
			problem = error.message;
			return undefined;
		});
		Promise.all([loadThemes(), saved]).then(
			([found, slug]) => {
				themes = found;
				if (slug === undefined) return;
				const theme = found.find((t) => t.slug === slug) ?? null;
				chosen = theme?.slug ?? DEFAULT;
				useTheme(theme);
			},
			(error: Error) => (problem = error.message)
		);
	});

	function choose(slug: string): void {
		chosen = slug;
		useTheme(themes.find((t) => t.slug === slug) ?? null);
		saveTheme(slug || null).then(
			() => (problem = null),
			(error: Error) => (problem = error.message)
		);
	}
</script>

<label class="picker">
	<span>Theme</span>
	<select
		value={chosen}
		onchange={(e) => choose(e.currentTarget.value)}
		title={problem ?? undefined}
	>
		<option value={DEFAULT}>Follow the system</option>
		{#if themes.length === 0 && chosen !== DEFAULT}<option value={chosen}>{chosen}</option>{/if}
		<optgroup label="Light">
			{#each light as theme (theme.slug)}<option value={theme.slug}>{theme.name}</option>{/each}
		</optgroup>
		<optgroup label="Dark">
			{#each dark as theme (theme.slug)}<option value={theme.slug}>{theme.name}</option>{/each}
		</optgroup>
	</select>
</label>

<style>
	.picker {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-left: auto;
		color: var(--hc-muted);
		white-space: nowrap;
	}

	select {
		font: inherit;
		background: var(--hc-bg);
		color: var(--hc-fg);
		border: 1px solid var(--hc-border);
		border-radius: var(--hc-radius);
		max-width: 12rem;
	}
</style>
