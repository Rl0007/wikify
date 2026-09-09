<script setup>
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Badge, Button, Dropdown, Popover, useList } from "frappe-ui";
import { CodeEditor } from "frappe-ui/code-editor";
import { Splitpanes, Pane } from "splitpanes";
import "splitpanes/dist/splitpanes.css";
import MarkdownPreview from "@/components/MarkdownPreview.vue";
import { useIsNarrow, useMediaQuery } from "@/composables/useMediaQuery";
import { setPage } from "@/data/agentContext";

const props = defineProps({
	sourceDocument: { type: String, default: null },
});

const route = useRoute();
const router = useRouter();

const pages = useList({
	doctype: "Source Page",
	fields: [
		"name",
		"page_no",
		"kind",
		"image",
		"baseline_markdown",
		"verdict",
		"composite",
		"text_recall",
		"extra_ratio",
		"table_score",
		"judge_score",
		"notes",
		"remediation_method",
		"remediation_adopted",
		"remediation_composite",
		"remediation_notes",
		"remediation_markdown",
		"canonical_source",
		"canonical_composite",
		"canonical_markdown",
		"llm_cost",
	],
	filters: computed(() => ({ source_document: props.sourceDocument || "__none__" })),
	orderBy: "page_no asc",
	limit: 1000,
	auto: true,
});

// Let the parent (ImportDetail) refetch after a remediation run completes.
defineExpose({ reload: () => pages.reload() });

// Filter + selected page are mirrored in the route query (?filter=&page=) so a refresh
// or shared link restores the exact view. All by default; Flagged = pages needing review
// (verdict ≠ pass), Passed = its negation.
const FILTERS = ["all", "flagged", "passed"];
const filter = ref(FILTERS.includes(route.query.filter) ? route.query.filter : "all");

const visiblePages = computed(() => {
	const all = pages.data || [];
	if (filter.value === "flagged") return all.filter((p) => p.verdict !== "pass");
	if (filter.value === "passed") return all.filter((p) => p.verdict === "pass");
	return all;
});

const selectedName = ref(null);
const selected = computed(
	() => (pages.data || []).find((p) => p.name === selectedName.value) || null
);

// Narrow screens drill down instead of splitting: the list fills the width, tapping a
// page swaps in its detail, Back returns. A ?page= deep link opens straight on detail.
// Swapping the Splitpanes host for a plain <div> keeps one copy of the markup rather
// than forking a phone-only template.
const isNarrow = useIsNarrow();
const showDetail = ref(!!route.query.page);
const SplitHost = computed(() => (isNarrow.value ? "div" : Splitpanes));
const SplitPane = computed(() => (isNarrow.value ? "div" : Pane));
function openPage(name) {
	selectedName.value = name;
	showDetail.value = true;
}

// Attach the selected page as the agent's default context (swaps out any section chip).
watch(selected, (p) => {
	if (p) setPage({ name: p.name, label: `Page ${p.page_no}` });
});

// First resolution restores the selection from ?page=<page_no>; afterwards just keep a
// valid selection as the list / filter changes.
let restored = false;
watch(
	visiblePages,
	(list) => {
		if (!list.length) {
			selectedName.value = null;
			return;
		}
		if (!restored) {
			restored = true;
			const want = route.query.page ? Number(route.query.page) : null;
			const match = want ? list.find((p) => p.page_no === want) : null;
			selectedName.value = (match || list[0]).name;
			return;
		}
		if (!list.some((p) => p.name === selectedName.value)) {
			selectedName.value = list[0].name;
		}
	},
	{ immediate: true }
);

// Persist filter + selected page_no into the query (replace; keep the default tidy).
watch([filter, selected], () => {
	const query = { ...route.query };
	if (filter.value === "all") delete query.filter;
	else query.filter = filter.value;
	if (selected.value) query.page = String(selected.value.page_no);
	else delete query.page;
	if (query.filter !== route.query.filter || query.page !== route.query.page) {
		router.replace({ query });
	}
});

const totalCount = computed(() => (pages.data || []).length);
const flaggedCount = computed(() => (pages.data || []).filter((p) => p.verdict !== "pass").length);
const passedCount = computed(() => (pages.data || []).filter((p) => p.verdict === "pass").length);
const filterOptions = computed(() => [
	{ label: "All", key: "all", count: totalCount.value },
	{ label: "Flagged", key: "flagged", count: flaggedCount.value },
	{ label: "Passed", key: "passed", count: passedCount.value },
]);

// Wide viewports show the page image and the rendered result side-by-side (0.4 slice
// 23) — the Page tab only exists in the narrow fallback, where the split collapses
// back to tabs. Viewing the whole PDF lives at the document level (ImportDetail's
// top-level PDF tab), since it isn't page-specific.
const isWide = useMediaQuery("(min-width: 1100px)");

// Icon-only tabs (tooltip carries the name) — same icons WikiPreview's toggle uses.
const tabs = computed(() =>
	isWide.value
		? [
				{ label: "Preview", key: "preview", icon: "lucide-eye" },
				{ label: "Markdown", key: "markdown", icon: "lucide-code" },
		  ]
		: [
				{ label: "Page", key: "page", icon: "lucide-image" },
				{ label: "Preview", key: "preview", icon: "lucide-eye" },
				{ label: "Markdown", key: "markdown", icon: "lucide-code" },
		  ]
);
const activeTab = ref(isWide.value ? "preview" : "page");
watch(isWide, (wide) => {
	if (wide && activeTab.value === "page") activeTab.value = "preview";
});

// User-draggable image∥preview ratio, persisted like other pane sizes. Clamped on read:
// a stored size dragged to the edge on a desktop would otherwise strand the next visitor
// with a few unreadable pixels of one pane.
const SPLIT_KEY = "wikify:pageReviewSplit";
const MIN_SPLIT = 20;
const imgPaneSize = ref(clampSplit(Number(localStorage.getItem(SPLIT_KEY))));
function clampSplit(size) {
	if (!size) return 50;
	return Math.min(100 - MIN_SPLIT, Math.max(MIN_SPLIT, size));
}
function onSplitResized(event) {
	const size = (event?.panes || event)?.[0]?.size;
	if (size) {
		imgPaneSize.value = clampSplit(size);
		localStorage.setItem(SPLIT_KEY, String(imgPaneSize.value));
	}
}

// Fit-to-width by default; click toggles natural size. Reset per page.
const zoomed = ref(false);
watch(selected, () => (zoomed.value = false));

const verdictTheme = { pass: "green", escalate: "orange", review: "red" };

// One content view: the resultant markdown (canonical, else baseline) — what
// sectionize consumes and what edits change. Baseline / remediation are pipeline
// internals, reachable from the ⋯ menu for diagnosis; reset per page.
const mdView = ref("result");
watch(selected, () => (mdView.value = "result"));
const mdSources = computed(() => {
	const p = selected.value;
	const sources = [{ label: "Result", key: "result" }];
	if (p?.remediation_method) sources.push({ label: "Remediation", key: "remediation" });
	if (p?.canonical_source) sources.push({ label: "Baseline", key: "baseline" });
	return sources;
});
const sourceMenu = computed(() =>
	mdSources.value.map((s) => ({
		label: s.label,
		icon: mdView.value === s.key ? "lucide-check" : undefined,
		onClick: () => (mdView.value = s.key),
	}))
);
const mdContent = computed(() => {
	const p = selected.value;
	if (!p) return "";
	if (mdView.value === "remediation") return p.remediation_markdown || "";
	if (mdView.value === "baseline") return p.baseline_markdown || "";
	return p.canonical_markdown || p.baseline_markdown || "";
});

// 0.0 reads as "n/a" for table/judge (no table on the page / not judged). A genuine
// table miss still surfaces via the harness notes, so hiding the bare 0 is honest.
function fmt(v) {
	return v === null || v === undefined ? "—" : Number(v).toFixed(2);
}
function fmtOptional(v) {
	return v ? Number(v).toFixed(2) : "—";
}

// The user-facing surface is verdict + audit score + cost (0.4 slice 23). The audit
// score is the canonical composite once remediation has produced one, else baseline.
function pageAudit(p) {
	return p.canonical_source && p.canonical_composite ? p.canonical_composite : p.composite;
}
const auditScore = computed(() => (selected.value ? pageAudit(selected.value) : null));
function fmtCost(v) {
	return v ? `$${Number(v).toFixed(4)}` : "—";
}

// Diagnostic sub-metrics live in the Details popover: text pages show the full
// deterministic set; visual pages drop recall/extra (meaningless on diagrams).
const scoreCells = computed(() => {
	const p = selected.value;
	if (!p) return [];
	if (p.kind === "visual") {
		return [
			{ label: "Judge", value: fmtOptional(p.judge_score) },
			{ label: "Table", value: fmtOptional(p.table_score) },
			{ label: "Composite", value: fmt(p.composite), strong: true },
		];
	}
	return [
		{ label: "Recall", value: fmt(p.text_recall) },
		{ label: "Extra", value: fmt(p.extra_ratio) },
		{ label: "Table", value: fmtOptional(p.table_score) },
		{ label: "Judge", value: fmtOptional(p.judge_score) },
		{ label: "Composite", value: fmt(p.composite), strong: true },
	];
});

// Before↔after summary once a page has been remediated: which method ran, whether
// it was adopted as canonical, and the baseline→remediation composite delta.
const remediation = computed(() => {
	const p = selected.value;
	if (!p?.remediation_method) return null;
	const delta = Number(p.remediation_composite || 0) - Number(p.composite || 0);
	return {
		method: p.remediation_method,
		adopted: !!p.remediation_adopted,
		base: p.composite,
		after: p.remediation_composite,
		delta,
		canonical: p.canonical_composite,
		notes: p.remediation_notes,
	};
});
function fmtDelta(v) {
	const n = Number(v || 0);
	return `${n >= 0 ? "+" : ""}${n.toFixed(3)}`;
}
</script>

<template>
	<div class="h-full">
		<p v-if="!pages.data?.length" class="py-10 text-center text-sm text-ink-gray-5">
			No pages yet — parse still running or not started.
		</p>

		<component :is="SplitHost" v-else :class="isNarrow ? 'flex h-full flex-col' : 'h-full'">
			<!-- Left: thumbnail list -->
			<component
				:is="SplitPane"
				v-show="!isNarrow || !showDetail"
				:size="isNarrow ? undefined : 30"
				:min-size="isNarrow ? undefined : 20"
				class="flex flex-col"
				:class="isNarrow ? 'min-h-0 flex-1' : 'border-r border-outline-gray-1'"
			>
				<div
					class="flex items-center gap-1 overflow-x-auto border-b border-outline-gray-1 px-3 py-2"
				>
					<Button
						v-for="f in filterOptions"
						:key="f.key"
						:label="`${f.label} (${f.count})`"
						size="sm"
						class="shrink-0"
						:variant="filter === f.key ? 'subtle' : 'ghost'"
						@click="filter = f.key"
					/>
				</div>
				<div class="flex-1 overflow-y-auto p-2">
					<p
						v-if="!visiblePages.length"
						class="px-2 py-6 text-center text-sm text-ink-gray-5"
					>
						{{
							filter === "flagged"
								? "No flagged pages — everything passed."
								: filter === "passed"
								? "No passed pages yet."
								: "No pages."
						}}
					</p>
					<button
						v-for="page in visiblePages"
						:key="page.name"
						class="mb-1 flex w-full items-center gap-2 rounded-md p-1.5 text-left hover:bg-surface-gray-2"
						:class="selectedName === page.name && !isNarrow ? 'bg-surface-gray-3' : ''"
						@click="openPage(page.name)"
					>
						<img
							v-if="page.image"
							:src="page.image"
							:alt="`Page ${page.page_no}`"
							class="h-14 w-11 shrink-0 rounded border border-outline-gray-1 object-cover object-top"
						/>
						<div class="min-w-0 flex-1">
							<div class="flex flex-wrap items-center gap-1.5">
								<span class="text-sm font-medium text-ink-gray-8"
									>Page {{ page.page_no }}</span
								>
								<Badge
									:label="page.kind"
									:theme="page.kind === 'visual' ? 'orange' : 'gray'"
									variant="subtle"
									size="sm"
								/>
							</div>
							<div class="mt-1 flex flex-wrap items-center gap-1.5">
								<Badge
									:label="page.verdict || '—'"
									:theme="verdictTheme[page.verdict] || 'gray'"
									variant="subtle"
									size="sm"
								/>
								<span class="text-xs text-ink-gray-5">{{
									fmt(pageAudit(page))
								}}</span>
								<Badge
									v-if="page.remediation_adopted"
									label="remediated"
									theme="blue"
									variant="subtle"
									size="sm"
								/>
							</div>
						</div>
					</button>
				</div>
			</component>

			<!-- Right: detail -->
			<component
				:is="SplitPane"
				v-show="!isNarrow || showDetail"
				:size="isNarrow ? undefined : 70"
				class="flex flex-col"
				:class="isNarrow ? 'min-h-0 flex-1' : ''"
			>
				<template v-if="selected">
					<!-- Audit strip: verdict + audit score + cost; sub-metrics in Details -->
					<div class="border-b border-outline-gray-1 px-4 py-3">
						<div class="flex flex-wrap items-center gap-x-5 gap-y-2">
							<div class="flex items-center gap-2">
								<Button
									v-if="isNarrow"
									size="sm"
									variant="ghost"
									icon="lucide-arrow-left"
									aria-label="Back to pages"
									@click="showDetail = false"
								/>
								<span class="text-base font-medium text-ink-gray-9"
									>Page {{ selected.page_no }}</span
								>
								<Badge
									:label="selected.verdict || '—'"
									:theme="verdictTheme[selected.verdict] || 'gray'"
									variant="subtle"
								/>
								<Badge
									:label="selected.kind"
									:theme="selected.kind === 'visual' ? 'orange' : 'gray'"
									variant="subtle"
								/>
								<Badge
									v-if="selected.remediation_adopted"
									label="remediated"
									theme="blue"
									variant="subtle"
								/>
							</div>
							<div class="flex items-baseline gap-1.5">
								<span class="text-xs uppercase tracking-wide text-ink-gray-5"
									>Audit</span
								>
								<span class="text-sm font-semibold tabular-nums text-ink-gray-9">{{
									fmt(auditScore)
								}}</span>
							</div>
							<div class="flex items-baseline gap-1.5">
								<span class="text-xs uppercase tracking-wide text-ink-gray-5"
									>Cost</span
								>
								<span class="text-sm tabular-nums text-ink-gray-7">{{
									fmtCost(selected.llm_cost)
								}}</span>
							</div>
							<Popover placement="bottom-end">
								<template #target="{ togglePopover }">
									<Button
										label="Details"
										size="sm"
										variant="ghost"
										@click="togglePopover()"
									/>
								</template>
								<template #body-main>
									<div class="w-72 p-3 sm:w-80">
										<div class="flex flex-wrap gap-x-5 gap-y-1">
											<div
												v-for="c in scoreCells"
												:key="c.label"
												class="flex items-baseline gap-1.5"
											>
												<span
													class="text-xs uppercase tracking-wide text-ink-gray-5"
													>{{ c.label }}</span
												>
												<span
													class="text-sm tabular-nums"
													:class="
														c.strong
															? 'font-semibold text-ink-gray-9'
															: 'text-ink-gray-7'
													"
													>{{ c.value }}</span
												>
											</div>
										</div>
										<p
											v-if="selected.kind === 'visual'"
											class="mt-1.5 text-xs text-ink-gray-5"
										>
											Recall / extra are not meaningful on visual pages —
											judged on the rendered image.
										</p>

										<!-- Remediation before↔after -->
										<div
											v-if="remediation"
											class="mt-2 flex flex-wrap items-center gap-2 border-t border-outline-gray-1 pt-2"
										>
											<Badge
												:label="remediation.method"
												theme="blue"
												variant="subtle"
												size="sm"
											/>
											<Badge
												:label="
													remediation.adopted
														? 'adopted'
														: 'kept baseline'
												"
												:theme="remediation.adopted ? 'green' : 'gray'"
												variant="subtle"
												size="sm"
											/>
											<span class="text-xs text-ink-gray-6">
												{{ fmt(remediation.base) }} →
												{{ fmt(remediation.after) }}
												<span
													class="ml-1 tabular-nums"
													:class="
														remediation.delta >= 0
															? 'text-ink-green-6'
															: 'text-ink-red-6'
													"
													>({{ fmtDelta(remediation.delta) }})</span
												>
											</span>
											<span class="text-xs text-ink-gray-5">
												canonical {{ fmt(remediation.canonical) }}
											</span>
										</div>
										<p
											v-if="remediation?.notes"
											class="mt-1 text-xs text-ink-gray-5"
										>
											{{ remediation.notes }}
										</p>
									</div>
								</template>
							</Popover>
						</div>
						<p v-if="selected.notes" class="mt-1.5 text-xs text-ink-amber-6">
							{{ selected.notes }}
						</p>
					</div>

					<!-- Wide: page image ∥ rendered result, side by side -->
					<div v-if="isWide" class="min-h-0 flex-1">
						<Splitpanes class="h-full" @resized="onSplitResized">
							<Pane :size="imgPaneSize" :min-size="20">
								<div
									class="h-full overflow-auto border-r border-outline-gray-1 p-4"
								>
									<img
										v-if="selected.image"
										:src="selected.image"
										:alt="`Page ${selected.page_no}`"
										class="rounded border border-outline-gray-1"
										:class="
											zoomed
												? 'max-w-none cursor-zoom-out'
												: 'mx-auto max-w-full cursor-zoom-in'
										"
										@click="zoomed = !zoomed"
									/>
									<p v-else class="text-sm text-ink-gray-5">
										No page image rendered.
									</p>
								</div>
							</Pane>
							<Pane :size="100 - imgPaneSize" class="flex min-h-0 flex-col">
								<div
									class="flex items-center gap-1 border-b border-outline-gray-1 px-3 py-1.5"
								>
									<Button
										v-for="t in tabs"
										:key="t.key"
										:icon="t.icon"
										:tooltip="t.label"
										:aria-label="t.label"
										size="sm"
										:variant="activeTab === t.key ? 'subtle' : 'ghost'"
										@click="activeTab = t.key"
									/>
									<div class="ml-auto flex items-center gap-1">
										<Badge
											v-if="mdView !== 'result'"
											:label="mdView"
											theme="orange"
											variant="subtle"
											size="sm"
										/>
										<Dropdown
											v-if="sourceMenu.length > 1"
											:options="sourceMenu"
											placement="bottom-end"
										>
											<Button
												icon="lucide-more-horizontal"
												size="sm"
												variant="ghost"
												aria-label="Markdown source"
											/>
										</Dropdown>
									</div>
								</div>
								<div class="min-h-0 flex-1 overflow-auto">
									<MarkdownPreview
										v-if="activeTab === 'preview'"
										:content="mdContent"
										class="p-4"
									/>
									<div v-else class="flex h-full flex-col p-3">
										<CodeEditor
											:model-value="mdContent"
											language="markdown"
											variant="outline"
											:disabled="true"
											class="min-h-0 flex-1"
										/>
									</div>
								</div>
							</Pane>
						</Splitpanes>
					</div>

					<!-- Narrow fallback: the split collapses back to Page/Preview/Markdown tabs -->
					<template v-else>
						<div
							class="flex items-center gap-1 border-b border-outline-gray-1 px-3 py-1.5"
						>
							<Button
								v-for="t in tabs"
								:key="t.key"
								:icon="t.icon"
								:tooltip="t.label"
								:aria-label="t.label"
								size="sm"
								:variant="activeTab === t.key ? 'subtle' : 'ghost'"
								@click="activeTab = t.key"
							/>
							<div
								v-if="activeTab !== 'page'"
								class="ml-auto flex items-center gap-1"
							>
								<Badge
									v-if="mdView !== 'result'"
									:label="mdView"
									theme="orange"
									variant="subtle"
									size="sm"
								/>
								<Dropdown
									v-if="sourceMenu.length > 1"
									:options="sourceMenu"
									placement="bottom-end"
								>
									<Button
										icon="lucide-more-horizontal"
										size="sm"
										variant="ghost"
										aria-label="Markdown source"
									/>
								</Dropdown>
							</div>
						</div>

						<div class="min-h-0 flex-1 overflow-auto">
							<!-- Page (rendered image of the original page) -->
							<div v-if="activeTab === 'page'" class="p-4">
								<img
									v-if="selected.image"
									:src="selected.image"
									:alt="`Page ${selected.page_no}`"
									class="mx-auto max-w-full rounded border border-outline-gray-1"
								/>
								<p v-else class="text-sm text-ink-gray-5">
									No page image rendered.
								</p>
							</div>

							<!-- Preview (formatted markdown + mermaid diagrams) -->
							<MarkdownPreview
								v-else-if="activeTab === 'preview'"
								:content="mdContent"
								class="p-4"
							/>

							<!-- Markdown (raw source) -->
							<div
								v-else-if="activeTab === 'markdown'"
								class="flex h-full flex-col p-3"
							>
								<CodeEditor
									:model-value="mdContent"
									language="markdown"
									variant="outline"
									:disabled="true"
									class="min-h-0 flex-1"
								/>
							</div>
						</div>
					</template>
				</template>
				<p v-else class="py-10 text-center text-sm text-ink-gray-5">
					Select a page to review.
				</p>
			</component>
		</component>
	</div>
</template>
