import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vite';

// One self-contained ES module that defines every element, with its own
// Svelte runtime: a plugin shares no JavaScript with the shell.
export default defineConfig({
	plugins: [svelte({ compilerOptions: { customElement: true } })],
	build: {
		lib: { entry: 'src/index.ts', formats: ['es'], fileName: () => 'index.js' },
		outDir: 'dist',
		sourcemap: true
	}
});
