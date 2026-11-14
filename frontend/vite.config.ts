import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// The kernel answers every unknown path with index.html.
export default defineConfig({
	plugins: [sveltekit({ adapter: adapter({ fallback: 'index.html' }) })],
	build: { sourcemap: true },
	server: {
		proxy: {
			'/api': 'http://localhost:8080',
			'/plugins': 'http://localhost:8080'
		}
	}
});
