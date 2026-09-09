<script setup>
/**
 * Document-scope graph route (0.5 Slice 27): /import/:name/graph — full-viewport
 * canvas under the standard 48px sticky header. Clicking a section lands in the
 * import's Tree tab with that section selected (`?section=` deep link).
 */
import { useRouter } from "vue-router";
import { Button, PageHeader, useDoc } from "frappe-ui";
import GraphView from "@/components/GraphView.vue";

const props = defineProps({
	name: { type: String, required: true },
});

const router = useRouter();
const imp = useDoc({ doctype: "Wikify Import", name: props.name });

function onSelect(node) {
	if (node.kind === "section") {
		router.push({
			name: "ImportDetail",
			params: { name: props.name, tab: "tree" },
			query: { section: node.id },
		});
	} else {
		router.push({ name: "ImportDetail", params: { name: props.name } });
	}
}
</script>

<template>
	<div class="flex h-full flex-col">
		<PageHeader>
			<div class="flex min-w-0 items-center gap-3">
				<Button
					variant="ghost"
					icon="lucide-arrow-left"
					:route="{ name: 'ImportDetail', params: { name: props.name } }"
				/>
				<RouterLink
					v-if="imp.doc?.project"
					:to="{ name: 'ProjectDetail', params: { name: imp.doc.project } }"
					class="hidden shrink-0 text-base text-ink-gray-5 hover:text-ink-gray-7 lg:block"
					>{{ imp.doc.project_name || "Project" }}
					<span class="text-ink-gray-4" aria-hidden="true">/</span></RouterLink
				>
				<RouterLink
					:to="{ name: 'ImportDetail', params: { name: props.name } }"
					class="truncate text-base text-ink-gray-5 hover:text-ink-gray-7"
					>{{ imp.doc?.import_title || props.name }}
					<span class="text-ink-gray-4" aria-hidden="true">/</span></RouterLink
				>
				<h1 class="shrink-0 text-md text-ink-gray-9">Graph</h1>
			</div>
		</PageHeader>

		<div class="min-h-0 flex-1">
			<GraphView
				v-if="imp.doc?.source_document"
				:url="'/api/v2/method/wikify.api.graph.get_document_graph'"
				:params="{ source_document: imp.doc.source_document }"
				@select="onSelect"
			/>
			<div v-else-if="imp.doc" class="grid h-full place-items-center">
				<p class="text-sm text-ink-gray-5">
					No parsed document yet — the graph appears once parsing completes.
				</p>
			</div>
		</div>
	</div>
</template>
