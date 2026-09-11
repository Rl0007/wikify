<script setup>
import { computed, ref, watch, onMounted, onUnmounted } from "vue";
import { Badge, Button, useCall, toast } from "frappe-ui";
import { useSocket } from "@/socket";
import TypeChip from "@/components/TypeChip.vue";
import { useIsMobile } from "@/composables/useMediaQuery";
import { actionButtonProps } from "@/utils/actionButton";

const props = defineProps({
	sourceDocument: { type: String, default: null },
	importName: { type: String, default: null },
});

const isMobile = useIsMobile();

// Fetches are driven explicitly via watchers (reactive `auto` on useCall is unreliable
// when the dep flips — the Slice 4 gotcha), so both calls are immediate:false + submit.
const summary = useCall({
	url: "/api/v2/method/wikify.api.explore.type_summary",
	method: "GET",
	immediate: false,
});
const results = useCall({
	url: "/api/v2/method/wikify.api.explore.sections_by_type",
	method: "GET",
	immediate: false,
});

function loadSummary() {
	if (props.sourceDocument) summary.submit({ source_document: props.sourceDocument });
}
function loadResults() {
	if (props.sourceDocument && selectedType.value) {
		results.submit({
			section_type: selectedType.value,
			source_document: props.sourceDocument,
		});
	}
}

// Only types that actually occur in this doc.
const chips = computed(() => (summary.data || []).filter((t) => t.count > 0));
const sections = computed(() => (results.data || []).flatMap((g) => g.sections));

const selectedType = ref(null);
watch(() => props.sourceDocument, loadSummary, { immediate: true });
watch(chips, (list) => {
	if (list.length && !list.some((t) => t.type_name === selectedType.value)) {
		selectedType.value = list[0].type_name;
	}
});
watch(selectedType, loadResults);

// Reclassify — re-tag after tree edits. Streams over realtime; refresh on completion.
const reclassifying = ref(false);
const reclassify = useCall({
	url: "/api/v2/method/wikify.api.imports.reclassify",
	method: "POST",
	immediate: false,
});
async function runReclassify() {
	reclassifying.value = true;
	await reclassify.submit({ import_name: props.importName });
	if (reclassify.error) {
		reclassifying.value = false;
		toast.error("Could not start reclassification");
	}
}

const socket = useSocket();
function onClassifyDone(payload) {
	if (payload.import !== props.importName) return;
	reclassifying.value = false;
	if (payload.error) {
		toast.error("Reclassification failed");
		return;
	}
	loadSummary();
	loadResults();
	toast.success("Sections reclassified");
}
// The agent's end-of-turn batch (0.4 slice 25) can retag/reshape sections — refresh
// the type summary + results when a structure-bearing layer changed on this document.
function onAgentMutation(payload) {
	const docs =
		payload.source_documents || (payload.source_document ? [payload.source_document] : []);
	if (!docs.includes(props.sourceDocument)) return;
	const layers = payload.layers;
	if (!layers || ["tree", "section", "taxonomy"].some((l) => layers.includes(l))) {
		loadSummary();
		loadResults();
	}
}
onMounted(() => {
	socket?.on("wikify_classify_done", onClassifyDone);
	socket?.on("wikify_agent_mutation", onAgentMutation);
});
onUnmounted(() => {
	socket?.off("wikify_classify_done", onClassifyDone);
	socket?.off("wikify_agent_mutation", onAgentMutation);
});

function pageRange(s) {
	if (s.page_start == null) return "";
	return s.page_start === s.page_end ? `p${s.page_start}` : `p${s.page_start}–${s.page_end}`;
}
</script>

<template>
	<div class="flex h-full flex-col">
		<!-- Chip bar + reclassify -->
		<div class="flex items-start gap-2 border-b border-outline-gray-1 px-3 py-2">
			<div class="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
				<TypeChip
					v-for="t in chips"
					:key="t.type_name"
					:label="t.label"
					:color="t.color"
					:count="t.count"
					:active="t.type_name === selectedType"
					@click="selectedType = t.type_name"
				/>
				<span v-if="!chips.length && !summary.loading" class="text-sm text-ink-gray-5">
					No sections classified yet.
				</span>
			</div>
			<Button
				size="sm"
				variant="subtle"
				class="shrink-0"
				v-bind="
					actionButtonProps(
						isMobile,
						'lucide-tags',
						reclassifying ? 'Classifying…' : 'Reclassify'
					)
				"
				:loading="reclassifying"
				:disabled="reclassifying"
				@click="runReclassify"
			/>
		</div>

		<!-- Matching sections (single doc → flat list) -->
		<div class="min-h-0 flex-1 overflow-auto">
			<div
				v-if="sections.length"
				class="flex items-center gap-3 border-b border-outline-gray-1 px-4 py-2 text-sm text-ink-gray-5"
			>
				<span class="flex-1">Route ({{ sections.length }})</span>
				<span class="w-20 shrink-0 text-right">Pages</span>
			</div>
			<div
				v-for="s in sections"
				:key="s.name"
				class="flex items-start gap-3 border-b border-outline-gray-1 px-4 py-2.5 last:border-b-0"
			>
				<div class="min-w-0 flex-1">
					<p class="text-base break-words text-ink-gray-8">
						{{ s.hierarchy_path || s.title }}
					</p>
					<p class="truncate text-xs text-ink-gray-5">{{ s.title }}</p>
				</div>
				<!-- The badge sizes to its text inside a fixed column, so it reads as a chip
				     rather than an 80px pill with the range shoved to one end. -->
				<span class="flex w-20 shrink-0 justify-end">
					<Badge
						v-if="pageRange(s)"
						:label="pageRange(s)"
						theme="gray"
						variant="subtle"
						size="sm"
					/>
				</span>
			</div>
			<p
				v-if="!sections.length && selectedType && !results.loading"
				class="py-10 text-center text-sm text-ink-gray-5"
			>
				No sections of this type.
			</p>
		</div>
	</div>
</template>
