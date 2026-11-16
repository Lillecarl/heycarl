<script lang="ts">
	import { onMount } from 'svelte';
	import {
		loadThemes,
		savedTheme,
		saveTheme,
		storedTheme,
		useTheme,
		type Theme
	} from '#lib/theme.ts';
	import ErrorNotice from '@heycarl/sdk/ErrorNotice.svelte';

	const DEFAULT = '';

	let themes: Theme[] = $state.raw([]);
	let chosen = $state(storedTheme() ?? DEFAULT);
	let problem: unknown = $state(null);

	const light = $derived(themes.filter((t) => t.variant === 'light'));
	const dark = $derived(themes.filter((t) => t.variant === 'dark'));

	const report = (what: string) => (error: unknown) => {
		problem = new Error(what, { cause: error });
	};

	onMount(() => {
		// Without the saved row, this browser's copy stands.
		const saved = savedTheme().catch((e: unknown) => {
			report('The saved theme did not load')(e);
			return undefined;
		});
		Promise.all([loadThemes(), saved]).then(([found, slug]) => {
			themes = found;
			if (slug === undefined) return;
			const theme = found.find((t) => t.slug === slug) ?? null;
			chosen = theme?.slug ?? DEFAULT;
			useTheme(theme);
		}, report('The themes did not load'));
	});

	function choose(slug: string): void {
		chosen = slug;
		useTheme(themes.find((t) => t.slug === slug) ?? null);
		saveTheme(slug || null).then(() => (problem = null), report('The theme was not saved'));
	}
</script>

<label class="picker">
	<span>Theme</span>
	<select value={chosen} onchange={(e) => choose(e.currentTarget.value)}>
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
{#if problem}<div class="problem"><ErrorNotice error={problem} /></div>{/if}

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
		color: var(--hc-fg);
		max-width: 12rem;
	}
	.problem {
		white-space: nowrap;
	}
	.problem > :global(p) {
		margin: 0;
	}
</style>
