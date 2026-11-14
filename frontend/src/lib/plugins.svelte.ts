import { api, type PluginInfo } from '@heycarl/sdk';

export interface Page {
	plugin: string;
	tag: string;
	title: string;
}

class Registry {
	plugins = $state<PluginInfo[]>([]);
	pages = $state<Page[]>([]);
	error = $state<string | null>(null);
	ready = $state(false);

	async load(): Promise<void> {
		try {
			this.plugins = await api<PluginInfo[]>('/api/plugins');
			// A plugin module defines its custom elements when it loads; one
			// that fails to load leaves the others working.
			const results = await Promise.allSettled(
				this.plugins
					.filter((p) => p.frontend)
					.map((p) => import(/* @vite-ignore */ p.frontend!.module))
			);
			const failed = results.filter((r) => r.status === 'rejected');
			if (failed.length) this.error = `${failed.length} plugin module(s) failed to load`;
			this.pages = this.plugins.flatMap((p) =>
				Object.entries(p.frontend?.pages ?? {}).map(([tag, title]) => ({
					plugin: p.name,
					tag,
					title
				}))
			);
		} catch (e) {
			this.error = String(e);
		} finally {
			this.ready = true;
		}
	}
}

export const registry = new Registry();
