<script lang="ts">
	let {
		contact,
		big = false
	}: { contact: { id: number; first_name: string; last_name: string }; big?: boolean } = $props();

	const initials = $derived(
		(contact.first_name.slice(0, 1) + contact.last_name.slice(0, 1)).toUpperCase()
	);
	// A stable colour per contact, from the gruvbox bright palette.
	const PALETTE = ['#fb4934', '#b8bb26', '#fabd2f', '#83a598', '#d3869b', '#8ec07c', '#fe8019'];
	const colour = $derived(PALETTE[contact.id % PALETTE.length]);
</script>

<span class="avatar" class:big style:background={colour} aria-hidden="true">{initials}</span>
