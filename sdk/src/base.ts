/// <reference path="./css.d.ts" />
import css from './base.css?inline';

let sheet: CSSStyleSheet | null = null;

/**
 * The `extend` for a plugin's custom element: its shadow root adopts the
 * shared base sheet, so no plugin copies the base styles.
 *
 * <svelte:options customElement={{ tag: 'heycarl-x', extend: withBase }} />
 */
export function withBase(element: new () => HTMLElement): new () => HTMLElement {
	return class extends element {
		constructor() {
			super();
			if (!sheet) {
				sheet = new CSSStyleSheet();
				sheet.replaceSync(css);
			}
			const root = this.shadowRoot;
			if (root) root.adoptedStyleSheets = [sheet, ...root.adoptedStyleSheets];
		}
	};
}
