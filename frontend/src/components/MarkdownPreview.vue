<script setup>
// Rendered-markdown view shared by the page Preview tab and the Tree section body.
// Renders markdown with `marked` (prose-styled) and post-processes ```mermaid fences
// into SVG diagrams (see utils/mermaid).
import { computed, ref, watch, nextTick, onMounted } from "vue";
import { marked } from "marked";
import { renderMermaidIn } from "@/utils/mermaid";

const props = defineProps({
	content: { type: String, default: "" },
});

const container = ref(null);
const html = computed(() => marked.parse(props.content || "", { async: false }));

async function renderDiagrams() {
	await nextTick();
	await renderMermaidIn(container.value);
}

onMounted(renderDiagrams);
watch(() => props.content, renderDiagrams);
</script>

<template>
	<div
		ref="container"
		class="markdown-body prose prose-sm dark:prose-invert max-w-none"
		v-html="html"
	/>
</template>

<style>
/* Page markdown carries wide tables and unbroken identifiers. Below the split
   breakpoint each table becomes its own horizontal scroller and long words wrap, so a
   phone-width column never has to widen to fit them. Wide viewports are untouched. */
@media (max-width: 1023px) {
	.markdown-body table {
		display: block;
		width: max-content;
		max-width: 100%;
		overflow-x: auto;
	}

	.markdown-body {
		overflow-wrap: break-word;
	}
}
</style>
