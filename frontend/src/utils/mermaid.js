// Mermaid rendering for markdown previews.
//
// Mermaid is a real frontend dependency loaded through a dynamic `import()`, so Vite
// code-splits it into its own chunk (~3 MB) that is fetched only when a preview
// actually contains a diagram. The Frappe Wiki app is NOT a dependency here: it ships
// mermaid only as a hashed chunk inside its own SPA bundle, so `/assets/wiki/js/...`
// 404s and every diagram used to degrade silently to a plain code block. If some other
// app on the site happens to have published `window.wikiGetMermaid`, we take it as a
// free fast path, but nothing depends on it.

import "./mermaid.css";

let mermaidPromise = null;
let counter = 0;

async function getMermaid() {
	if (!mermaidPromise) {
		mermaidPromise = window.wikiGetMermaid
			? Promise.resolve(window.wikiGetMermaid())
			: import("mermaid").then((module) => module.default);
	}
	return mermaidPromise;
}

function getThemeConfig() {
	if (window.wikiMermaidThemeConfig) return window.wikiMermaidThemeConfig();
	const isDark = document.documentElement.getAttribute("data-theme") === "dark";
	return { theme: isDark ? "dark" : "default" };
}

function formatFailure(source, message) {
	const figure = document.createElement("div");
	figure.className = "mermaid-figure mermaid-figure-failed";
	figure.setAttribute("data-mermaid-done", "");

	const chip = document.createElement("p");
	chip.className =
		"mermaid-error-chip inline-flex items-center gap-1 rounded border border-outline-red-2 bg-surface-red-1 px-2 py-1 text-xs text-ink-red-4";
	// Mermaid parse errors carry a multi-line ASCII caret diagram; the first line names
	// the problem and the source below already shows where it is.
	chip.textContent = `Diagram failed to render — ${String(message).split("\n")[0]}`;

	const code = document.createElement("pre");
	code.className = "mermaid-error-source";
	code.textContent = source;

	figure.append(chip, code);
	return figure;
}

/**
 * Find mermaid blocks inside `root` and replace each with its SVG. Handles both the
 * browser `marked` output (`<pre><code class="language-mermaid">`, used by
 * MarkdownPreview) and the wiki renderer's server-side output (`<pre class="mermaid">`,
 * used by WikiPreview) so the same util covers both previews.
 * Idempotent per element; a block that cannot render shows its source plus an error chip.
 */
export async function renderMermaidIn(root) {
	if (!root) return;
	const blocks = [...root.querySelectorAll("code.language-mermaid, pre.mermaid")].filter(
		(element) => !element.closest("[data-mermaid-done]")
	);
	if (!blocks.length) return;
	// Claim the blocks before the first await so a re-entrant call (content watcher firing
	// mid-load) cannot pick up the same ones.
	const targets = blocks.map((element) => {
		const host = element.closest("pre") || element;
		host.setAttribute("data-mermaid-done", "");
		return { host, source: element.textContent || "" };
	});

	let mermaid;
	try {
		mermaid = await getMermaid();
		mermaid.initialize({ startOnLoad: false, securityLevel: "strict", ...getThemeConfig() });
	} catch (error) {
		for (const { host, source } of targets) {
			host.replaceWith(formatFailure(source, error.message || "mermaid failed to load"));
		}
		return;
	}

	for (const { host, source } of targets) {
		counter += 1;
		try {
			const { svg } = await mermaid.render(`wikify-mermaid-${counter}`, source);
			const figure = document.createElement("div");
			figure.className = "mermaid-figure";
			figure.setAttribute("data-mermaid-done", "");
			figure.innerHTML = svg;
			host.replaceWith(figure);
		} catch (error) {
			host.replaceWith(formatFailure(source, error.message || "invalid mermaid syntax"));
			// A failed render leaves mermaid's offscreen sandbox element behind.
			document.getElementById(`dwikify-mermaid-${counter}`)?.remove();
		}
	}
}
