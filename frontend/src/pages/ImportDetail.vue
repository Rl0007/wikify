<script setup>
import { computed, ref, watch, onMounted, onUnmounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
	Badge,
	Button,
	Dropdown,
	PageHeader,
	Progress,
	Tabs,
	useCall,
	useDoc,
	useList,
} from "frappe-ui";
import { useSocket } from "@/socket";
import { statusTheme, isActive } from "@/utils/status";
import PageReview from "@/components/PageReview.vue";
import SectionTree from "@/components/SectionTree.vue";
import Explore from "@/components/Explore.vue";
import WikiGenerate from "@/components/WikiGenerate.vue";
import { setDocument, setProject } from "@/data/agentContext";

const props = defineProps({
	name: { type: String, required: true },
	tab: { type: String, default: "pdf" },
});

const route = useRoute();
const router = useRouter();

const imp = useDoc({ doctype: "Wikify Import", name: props.name });

// PDF first (the source is the starting point); the metadata + streaming log ("Logs")
// moves to the end. The `overview` key is kept so its panel/v-ifs don't churn.
const tabs = [
	{ label: "PDF", key: "pdf" },
	{ label: "Pages", key: "pages" },
	{ label: "Tree", key: "tree" },
	{ label: "Explore", key: "explore" },
	{ label: "Wiki", key: "wiki" },
	{ label: "Logs", key: "overview" },
];
const tabKeys = tabs.map((t) => t.key);
const activeTab = ref(Math.max(0, tabKeys.indexOf(props.tab)));

// Persist the active tab in the route (path param) so a refresh restores it. Use
// replace so tab-switching doesn't flood browser history; preserve any query (the
// Pages filter/page sub-state lives there).
watch(activeTab, (i) => {
	const key = tabKeys[i] ?? "pdf";
	if (route.params.tab !== key) {
		router.replace({
			name: "ImportDetail",
			params: { name: props.name, tab: key },
			query: route.query,
		});
	}
});
// Reflect external route changes (back/forward, deep link) back into the tab.
watch(
	() => props.tab,
	(key) => {
		const i = tabKeys.indexOf(key);
		if (i >= 0 && i !== activeTab.value) activeTab.value = i;
	}
);

// Streaming log
const logs = useList({
	doctype: "Import Log Entry",
	fields: ["name", "idx_seq", "level", "stage", "message", "creation"],
	filters: { import: props.name },
	orderBy: "idx_seq asc",
	limit: 500,
});

const status = computed(() => imp.doc?.status);
const canRemediate = computed(() => status.value === "Review" && !!imp.doc?.source_document);

// Attach the document (with its project) as the agent's default context. Keyed on the
// source_document id so reloads (progress ticks) don't reset a page/section selection.
watch(
	() => imp.doc?.source_document,
	(sd) => {
		const projectChip = imp.doc?.project
			? { name: imp.doc.project, label: imp.doc.project_name || imp.doc.project }
			: null;
		if (sd) setDocument({ name: sd, label: imp.doc?.import_title || props.name }, projectChip);
		else if (projectChip) setProject(projectChip);
	},
	{ immediate: true }
);

const pageReview = ref(null);
const sectionTree = ref(null);

// Document-level audit score + LLM spend (0.4 slice 23) live on Source Document.
const sdStats = useList({
	doctype: "Source Document",
	fields: ["name", "mean_score", "canonical_mean", "llm_cost"],
	filters: computed(() => ({ name: imp.doc?.source_document || "__none__" })),
	limit: 1,
	auto: true,
});
const sourceStats = computed(() => sdStats.data?.[0] || null);
const docAudit = computed(
	() => sourceStats.value?.canonical_mean ?? sourceStats.value?.mean_score
);
function fmtCost(v) {
	return v ? `$${Number(v).toFixed(4)}` : "—";
}

// Remediation — route flagged (or all) pages through cleanup/VLM, adopt the best.
const remediate = useCall({
	url: "/api/v2/method/wikify.api.imports.trigger_remediation",
	method: "POST",
	immediate: false,
});
function runRemediation(scope) {
	remediate.submit({ import_name: props.name, scope });
}

// Realtime
const socket = useSocket();
function onProgress(payload) {
	if (payload.import !== props.name || !imp.doc) return;
	const wasRemediating = imp.doc.status === "Remediating";
	imp.doc.stage_progress = payload.percent;
	if (payload.status) imp.doc.status = payload.status;
	// Terminal transitions carry fields set server-side (source_document, wiki_space, error).
	if (["Review", "Failed", "Completed", "Graphed"].includes(payload.status)) {
		imp.reload();
		// A finished remediation rewrote canonical scores + rebuilt the tree — refetch both.
		if (wasRemediating && payload.status === "Review") {
			pageReview.value?.reload();
			sectionTree.value?.reload();
		}
		sdStats.reload();
	}
}
function onLog(payload) {
	if (payload.import !== props.name) return;
	if (Array.isArray(logs.data)) {
		logs.data.push({
			name: `live-${payload.idx_seq}`,
			idx_seq: payload.idx_seq,
			level: payload.level,
			stage: payload.stage,
			message: payload.message,
		});
	}
}
// The AI agent's write tools change this document's data from the chat panel. Since
// 0.4 slice 25 the loop emits ONE aggregated event per turn (at answer-completion /
// confirm pauses) instead of one per tool — refetch everything the batch touched.
function onAgentMutation(payload) {
	const docs =
		payload.source_documents || (payload.source_document ? [payload.source_document] : []);
	if (!imp.doc || !docs.includes(imp.doc.source_document)) return;
	pageReview.value?.reload();
	sectionTree.value?.reload();
	sdStats.reload();
}
onMounted(() => {
	socket?.on("wikify_import_progress", onProgress);
	socket?.on("wikify_import_log", onLog);
	socket?.on("wikify_agent_mutation", onAgentMutation);
});
onUnmounted(() => {
	socket?.off("wikify_import_progress", onProgress);
	socket?.off("wikify_import_log", onLog);
	socket?.off("wikify_agent_mutation", onAgentMutation);
});

const levelColor = { info: "text-ink-gray-7", warn: "text-ink-amber-6", error: "text-ink-red-6" };
</script>

<template>
	<div>
		<PageHeader>
			<div class="flex min-w-0 items-center gap-3">
				<Button
					variant="ghost"
					icon="lucide-arrow-left"
					:route="
						imp.doc?.project
							? { name: 'ProjectDetail', params: { name: imp.doc.project } }
							: { name: 'Projects' }
					"
				/>
				<RouterLink
					v-if="imp.doc?.project"
					:to="{ name: 'ProjectDetail', params: { name: imp.doc.project } }"
					class="shrink-0 text-base text-ink-gray-5 hover:text-ink-gray-7"
					>{{ imp.doc.project_name || "Project" }}
					<span class="text-ink-gray-4" aria-hidden="true">/</span></RouterLink
				>
				<h1 class="truncate text-md text-ink-gray-9">
					{{ imp.doc?.import_title || name }}
				</h1>
				<Badge
					v-if="status"
					:label="status"
					:theme="statusTheme(status)"
					variant="subtle"
				/>
				<Progress
					v-if="isActive(status)"
					:value="imp.doc?.stage_progress || 0"
					size="sm"
					class="w-40"
				/>
				<span v-if="isActive(status)" class="truncate text-sm text-ink-gray-5">{{
					imp.doc?.stage_label
				}}</span>
			</div>

			<div class="flex items-center gap-2">
				<Button
					v-if="imp.doc?.source_document"
					variant="subtle"
					label="Graph"
					icon-left="lucide-waypoints"
					:route="{ name: 'ImportGraph', params: { name: props.name } }"
				/>
				<Dropdown
					v-if="canRemediate"
					:options="[
						{ label: 'Remediate flagged', onClick: () => runRemediation('flagged') },
						{ label: 'Remediate all pages', onClick: () => runRemediation('all') },
					]"
				>
					<Button
						variant="solid"
						theme="gray"
						label="Remediate"
						icon-right="lucide-chevron-down"
						:loading="remediate.loading"
					/>
				</Dropdown>
			</div>
		</PageHeader>

		<Tabs v-model="activeTab" :tabs="tabs">
			<template #tab-panel="{ tab }">
				<!-- Overview -->
				<div v-if="tab.key === 'overview'" class="body-container pt-5 pb-40">
					<div class="grid grid-cols-2 gap-4 sm:grid-cols-4">
						<div>
							<p class="text-sm text-ink-gray-5">Pages</p>
							<p class="text-base text-ink-gray-8">
								{{ imp.doc?.page_count ?? "—" }}
							</p>
						</div>
						<div>
							<p class="text-sm text-ink-gray-5">Status</p>
							<p class="text-base text-ink-gray-8">{{ status }}</p>
						</div>
						<div>
							<p class="text-sm text-ink-gray-5">Started</p>
							<p class="text-base text-ink-gray-8">
								{{ imp.doc?.started_at || "—" }}
							</p>
						</div>
						<div>
							<p class="text-sm text-ink-gray-5">Completed</p>
							<p class="text-base text-ink-gray-8">
								{{ imp.doc?.completed_at || "—" }}
							</p>
						</div>
						<div>
							<p class="text-sm text-ink-gray-5">Audit score</p>
							<p class="text-base text-ink-gray-8">
								{{ docAudit != null ? Number(docAudit).toFixed(2) : "—" }}
							</p>
						</div>
						<div>
							<p class="text-sm text-ink-gray-5">LLM cost</p>
							<p class="text-base tabular-nums text-ink-gray-8">
								{{ fmtCost(sourceStats?.llm_cost) }}
							</p>
						</div>
					</div>

					<div
						v-if="imp.doc?.error"
						class="mt-5 rounded border border-outline-red-3 bg-surface-red-2 p-3"
					>
						<p class="mb-1 text-sm font-medium text-ink-red-6">Error</p>
						<pre class="overflow-x-auto whitespace-pre-wrap text-xs text-ink-red-6">{{
							imp.doc.error
						}}</pre>
					</div>

					<div class="mt-6">
						<p class="mb-2 text-sm text-ink-gray-5">Log</p>
						<div
							class="rounded-md border border-outline-gray-1 bg-surface-gray-1 p-3 font-mono text-xs"
						>
							<p v-if="!logs.data?.length" class="text-ink-gray-5">
								No log entries yet.
							</p>
							<div
								v-for="entry in logs.data"
								:key="entry.name"
								class="flex gap-2 py-0.5"
								:class="levelColor[entry.level] || 'text-ink-gray-7'"
							>
								<span class="shrink-0 text-ink-gray-4">[{{ entry.stage }}]</span>
								<span>{{ entry.message }}</span>
							</div>
						</div>
					</div>
				</div>

				<!-- Pages -->
				<div v-else-if="tab.key === 'pages'" class="h-[calc(100vh-7rem)]">
					<PageReview ref="pageReview" :source-document="imp.doc?.source_document" />
				</div>

				<!-- Tree -->
				<div v-else-if="tab.key === 'tree'" class="h-[calc(100vh-7rem)]">
					<SectionTree
						ref="sectionTree"
						:source-document="imp.doc?.source_document"
						:doc-title="imp.doc?.import_title || name"
						:import-name="name"
						:status="status"
						:initial-section="route.query.section"
						@graphed="imp.reload()"
					/>
				</div>

				<!-- Explore -->
				<div v-else-if="tab.key === 'explore'" class="h-[calc(100vh-7rem)]">
					<Explore :source-document="imp.doc?.source_document" :import-name="name" />
				</div>

				<!-- Wiki -->
				<div v-else-if="tab.key === 'wiki'" class="h-[calc(100vh-7rem)]">
					<WikiGenerate
						:source-document="imp.doc?.source_document"
						:import-name="name"
						:status="status"
						:wiki-space="imp.doc?.wiki_space"
						@generated="imp.reload()"
					/>
				</div>

				<!-- PDF (whole document) -->
				<div v-else-if="tab.key === 'pdf'" class="h-[calc(100vh-7rem)]">
					<object
						v-if="imp.doc?.pdf"
						:data="`${imp.doc.pdf}#view=FitH`"
						type="application/pdf"
						class="h-full w-full"
					>
						<p class="p-4 text-sm text-ink-gray-5">
							Can't embed the PDF here —
							<a :href="imp.doc.pdf" target="_blank" class="underline"
								>open it in a new tab</a
							>.
						</p>
					</object>
					<p v-else class="p-4 text-sm text-ink-gray-5">No PDF attached.</p>
				</div>
			</template>
		</Tabs>
	</div>
</template>
