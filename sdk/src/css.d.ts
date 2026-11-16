// Vite's ?inline import: the stylesheet's text, not a <style> in the page.
declare module '*.css?inline' {
	const css: string;
	export default css;
}
