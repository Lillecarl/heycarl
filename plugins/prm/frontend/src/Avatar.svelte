<script lang="ts">
	let {
		contact,
		big = false
	}: { contact: { id: number; first_name: string; last_name: string }; big?: boolean } = $props();

	const initials = $derived(
		(contact.first_name.slice(0, 1) + contact.last_name.slice(0, 1)).toUpperCase()
	);
	// A stable colour per contact, from the theme's base16 accent roles.
	const PALETTE = ['08', '0B', '0A', '0D', '0E', '0C', '09'].map((n) => `var(--hc-base${n})`);
	const colour = $derived(PALETTE[contact.id % PALETTE.length]);
</script>

<span class="avatar" class:big style:background={colour} aria-hidden="true">{initials}</span>

<style>
	.avatar {
		flex: none;
		display: grid;
		place-items: center;
		width: 2.25rem;
		height: 2.25rem;
		border-radius: 50%;
		font-weight: 600;
		color: var(--hc-accent-fg);
	}
	.big {
		width: 4rem;
		height: 4rem;
		font-size: 1.5rem;
	}
</style>
