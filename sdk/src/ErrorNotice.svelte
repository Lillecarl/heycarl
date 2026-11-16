<!--
	A failure, said for a person: <ErrorNotice {error} />. Renders nothing
	while `error` is null or undefined. Keep the thrown value as state; for
	context, throw `new Error('Could not save', { cause: e })`.
-->
<script lang="ts">
	import { errorText } from './errors.ts';

	let { error }: { error: unknown } = $props();
</script>

{#if error != null}<p class="error" role="alert">{errorText(error)}</p>{/if}
