// Mermaid rendering for markdown previews — reuses the Frappe Wiki app's vendored
// mermaid loader (wiki is an installed app and serves it at /assets/wiki/js/...). That
// keeps mermaid out of our bundle (~3 MB) and makes the preview theme identical to the
// published wiki, which reads its config from the live Frappe UI design tokens.
//
// The loader asset exposes:
//   window.wikiGetMermaid({ assetUrl })  → Promise<mermaid> (loaded + initialized)
//   window.wikiMermaidThemeConfig()      → theme config from current design tokens
// If the asset is unavailable (wiki not installed), rendering degrades gracefully:
// mermaid code fences are left as plain code blocks.

const LOADER_URL = "/assets/wiki/js/mermaid-loader.js";
const ASSET_URL = "/assets/wiki/js/vendor/mermaid/mermaid.min.js";

let loaderPromise = null;
let counter = 0;

function loadWikiMermaidLoader() {
	if (window.wikiGetMermaid) return Promise.resolve();
	if (!loaderPromise) {
		loaderPromise = new Promise((resolve, reject) => {
			const script = document.createElement("script");
			script.src = LOADER_URL;
			script.onload = () => resolve();
			script.onerror = () => reject(new Error("Unable to load the wiki mermaid loader"));
			document.head.appendChild(script);
		});
	}
	return loaderPromise;
}

async function getMermaid() {
	await loadWikiMermaidLoader();
	return window.wikiGetMermaid({ assetUrl: ASSET_URL });
}

function themeConfig() {
	return window.wikiMermaidThemeConfig ? window.wikiMermaidThemeConfig() : {};
}

/**
 * Find mermaid blocks inside `root` and replace each with its SVG. Handles both the
 * browser `marked` output (`<pre><code class="language-mermaid">`, used by
 * MarkdownPreview) and the wiki renderer's server-side output (`<pre class="mermaid">`,
 * used by WikiPreview) so the same util covers both previews.
 * Idempotent per element; failures leave the original block in place.
 */
export async function renderMermaidIn(root) {
	if (!root) return;
	const blocks = [...root.querySelectorAll("code.language-mermaid, pre.mermaid")].filter(
		(el) => !el.closest("[data-mermaid-done]")
	);
	if (!blocks.length) return;

	let mermaid;
	try {
		mermaid = await getMermaid();
		mermaid.initialize({ startOnLoad: false, securityLevel: "strict", ...themeConfig() });
	} catch {
		return; // mermaid asset unavailable — leave the code blocks untouched
	}

	for (const el of blocks) {
		const host = el.closest("pre") || el;
		host.setAttribute("data-mermaid-done", "");
		try {
			counter += 1;
			const { svg } = await mermaid.render(
				`wikify-mermaid-${counter}`,
				el.textContent || ""
			);
			const figure = document.createElement("div");
			figure.className = "mermaid-figure";
			figure.setAttribute("data-mermaid-done", "");
			figure.innerHTML = svg;
			host.replaceWith(figure);
		} catch {
			// Invalid diagram source — keep the raw fence (marked as done so we don't retry).
		}
	}
}
