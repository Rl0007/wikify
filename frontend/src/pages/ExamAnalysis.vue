<script setup>
// /exam-analysis — which topics the examiner keeps coming back to, and what to read for
// each. Rows are topics rolled up from the section tree, columns are exam years, and the
// shading is marks rather than question count: a 14-mark question is not the same event as
// a 2-mark MCQ, and counting them alike is how a heatmap starts lying.
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Badge, Button, Dialog, FormControl, PageHeader, Progress } from "frappe-ui";
import NewExamPaperDialog from "@/components/NewExamPaperDialog.vue";
import { useHeatmap, usePaperActions, useTopicDrilldown } from "@/composables/useExam";
import { useProjectOptions } from "@/composables/useRag";

const route = useRoute();
const router = useRouter();
const projectOptions = useProjectOptions();
const selected = ref(null);
const showWeighted = ref(true);
const showUpload = ref(false);
const showPapers = ref(true);
const confirmDelete = ref(null);
const actionNote = ref("");

const {
	load,
	scope,
	setScope,
	availableSources,
	grid,
	topics,
	years,
	papers,
	totals,
	peak,
	cell,
	errorText,
	loading,
} = useHeatmap();

// Destructured so the template auto-unwraps these refs. Reaching through the composable
// object (`drilldown.open.value`) works in script but reads badly in a v-model.
const {
	open: drillOpen,
	topic: drillTopic,
	year: drillYear,
	show: openDrilldown,
	questions: drillQuestions,
	sections: drillSections,
	loading: drillLoading,
	errorText: drillError,
} = useTopicDrilldown();

const { remove, reextract, remap, remapping, errorText: actionError } = usePaperActions();

// The options list leads with an "All projects" entry whose value is empty, and the heatmap
// is per-project by construction — so default to the first real project rather than landing
// every visitor on an empty state that looks like the feature is broken.
function firstRealProject(options) {
	return (options || []).find((option) => option.value)?.value || null;
}
onMounted(() => {
	// ?project= wins so a heatmap can be linked to directly — a student sharing "look at
	// Capital Gains" should not land the reader on whichever project happens to sort first.
	selected.value =
		route.query.project || selected.value || firstRealProject(projectOptions.value);
});
watch(projectOptions, (options) => {
	if (!selected.value) selected.value = route.query.project || firstRealProject(options);
});
watch(selected, (name) => {
	if (name && name !== route.query.project) {
		router.replace({ query: { ...route.query, project: name } });
	}
});
watch(selected, (name) => name && load(name), { immediate: true });

// Ranked by decayed score when weighting is on, by raw marks when it is off. Both are
// offered because the decay is an assumption, and a student deserves to see the ranking
// without it rather than take our weighting on faith.
const gaps = computed(() => grid.value?.gaps || []);
const scopedToAll = computed(() => !scope.value.length);

function toggleSource(name) {
	const next = scope.value.includes(name)
		? scope.value.filter((entry) => entry !== name)
		: [...scope.value, name];
	setScope(next);
}

const rankedTopics = computed(() => {
	const rows = [...topics.value];
	rows.sort((a, b) => (showWeighted.value ? b.score - a.score : b.marks - a.marks));
	return rows;
});

function intensity(topic, year) {
	const data = cell(topic, year);
	if (!data || !peak.value) return 0;
	return Math.min(1, data.marks / peak.value);
}

// Shading steps rather than a continuous ramp: discrete bands stay distinguishable to
// someone who cannot compare two similar blues, and each band keeps text contrast legible.
function cellClass(topic, year) {
	const value = intensity(topic, year);
	if (!value) return "bg-surface-gray-1 text-ink-gray-4";
	if (value < 0.2) return "bg-surface-blue-1 text-ink-gray-8";
	if (value < 0.45) return "bg-surface-blue-2 text-ink-gray-8";
	if (value < 0.7) return "bg-blue-300 text-ink-gray-9";
	return "bg-blue-500 text-white";
}

function cellLabel(topic, year) {
	const data = cell(topic, year);
	return data && data.marks ? Math.round(data.marks) : "";
}

// The stored `status` is pipeline vocabulary and it goes stale: mapping runs across the
// whole project, so a paper mapped by a later run keeps saying "Extracted". Six of nine
// papers read "Extracted" while every one of their questions was mapped. These read the
// derived `state` the API computes from what is actually true.
const STATE_LABELS = {
	ready: "Ready",
	working: "Working",
	unmapped: "Needs mapping",
	empty: "No questions",
	failed: "Failed",
};
const STATE_THEMES = {
	ready: "green",
	working: "blue",
	unmapped: "orange",
	empty: "gray",
	failed: "red",
};

function stateLabel(paper) {
	return STATE_LABELS[paper.state] || paper.status || "—";
}
function stateTheme(paper) {
	return STATE_THEMES[paper.state] || "gray";
}
function isWorking(paper) {
	return paper.state === "working";
}

// A freshly uploaded or re-extracted paper is still working when the dialog closes, so
// reload until it settles. Bounded rather than open-ended: if extraction dies the status
// stops changing, and an unbounded poll would hammer the server with nobody watching.
async function reloadUntilSettled() {
	for (let attempt = 0; attempt < 30; attempt++) {
		await load(selected.value);
		const busy = papers.value.some((paper) =>
			["Extracting", "Mapping", "Draft"].includes(paper.status),
		);
		if (!busy) return;
		await new Promise((resolve) => setTimeout(resolve, 5000));
	}
}

async function onReextract(paper) {
	actionNote.value = "";
	if (await reextract(paper.name)) {
		actionNote.value = `Re-extracting ${paper.paper_title}…`;
		await reloadUntilSettled();
		actionNote.value = "";
	}
}

async function onDelete(paper) {
	if (await remove(paper.name)) {
		confirmDelete.value = null;
		await load(selected.value);
	}
}

async function onRemap() {
	actionNote.value = "";
	const result = await remap(selected.value);
	if (result) {
		actionNote.value =
			`Re-mapped ${result.questions} questions · ` +
			`${Math.round((result.evidence_coverage || 0) * 100)}% evidence-backed`;
		await load(selected.value);
	}
}

// Chrome and Firefox both honour #page=N in a PDF URL, so a question can link straight to
// the page it was printed on instead of dropping the reader at page 1 of a 40-page paper.
function pdfLink(paper, page) {
	if (!paper?.source_pdf) return null;
	return page ? `${paper.source_pdf}#page=${page}` : paper.source_pdf;
}

const papersByName = computed(() =>
	Object.fromEntries((papers.value || []).map((paper) => [paper.name, paper])),
);
</script>

<template>
	<div class="flex h-full flex-col">
		<PageHeader>
			<div class="flex min-w-0 items-center gap-3">
				<h1 class="text-md text-ink-gray-9">Past paper analysis</h1>
				<span class="hidden truncate text-sm text-ink-gray-5 lg:inline">
					Topics the examiner returns to, weighted by marks and recency
				</span>
			</div>
			<div class="flex shrink-0 items-center gap-2">
				<Button
					:label="showPapers ? 'Hide papers' : 'Papers'"
					icon-left="lucide-files"
					@click="showPapers = !showPapers"
				/>
				<Button
					label="Re-run mapping"
					icon-left="lucide-refresh-cw"
					:loading="remapping"
					:disabled="!selected"
					@click="onRemap"
				/>
				<Button
					variant="solid"
					label="Add paper"
					icon-left="lucide-plus"
					:disabled="!selected"
					@click="showUpload = true"
				/>
				<div class="w-32 sm:w-52">
					<FormControl v-model="selected" type="select" :options="projectOptions" />
				</div>
			</div>
		</PageHeader>

		<div class="flex-1 overflow-y-auto px-5 pb-10">
			<p
				v-if="actionNote"
				class="mb-3 rounded bg-surface-blue-1 p-2 text-sm text-ink-blue-3"
			>
				{{ actionNote }}
			</p>
			<p v-if="actionError" class="mb-3 rounded bg-surface-red-1 p-2 text-sm text-ink-red-3">
				{{ actionError }}
			</p>

			<!-- Papers: the lifecycle surface. Open the PDF, re-extract a bad parse, delete. -->
			<div
				v-if="showPapers"
				class="mb-5 overflow-x-auto rounded border border-outline-gray-1 bg-surface-white"
			>
				<table class="w-full min-w-[46rem] text-sm">
					<thead>
						<tr
							class="border-b border-outline-gray-1 bg-surface-gray-1 text-ink-gray-7"
						>
							<th class="p-2 text-left font-medium">Paper</th>
							<th class="p-2 text-left font-medium">AY</th>
							<th class="p-2 text-right font-medium">Questions</th>
							<th class="p-2 text-right font-medium">Attemptable</th>
							<th class="p-2 text-left font-medium">Status</th>
							<th class="p-2 text-right font-medium">Actions</th>
						</tr>
					</thead>
					<tbody>
						<tr v-if="!papers.length">
							<td colspan="6" class="p-3 text-ink-gray-5">
								No question papers uploaded for this project yet.
							</td>
						</tr>
						<tr
							v-for="paper in papers"
							:key="paper.name"
							class="border-b border-outline-gray-1 last:border-0"
						>
							<td class="p-2">
								<a
									v-if="paper.source_pdf"
									:href="paper.source_pdf"
									target="_blank"
									rel="noopener"
									class="text-ink-blue-3 hover:underline"
								>
									{{ paper.paper_title }}
								</a>
								<span v-else class="text-ink-gray-8">{{ paper.paper_title }}</span>
							</td>
							<td class="p-2 text-ink-gray-6">{{ paper.assessment_year || "—" }}</td>
							<td class="p-2 text-right tabular-nums text-ink-gray-8">
								{{ paper.question_count }}
							</td>
							<td class="p-2 text-right tabular-nums text-ink-gray-8">
								{{ Math.round(paper.attempted_marks || 0) }}
							</td>
							<td class="p-2">
								<div class="flex items-center gap-2">
									<Badge :theme="stateTheme(paper)" size="sm">
										{{ stateLabel(paper) }}
									</Badge>
									<template v-if="isWorking(paper)">
										<Progress
											:value="paper.stage_progress || 0"
											size="sm"
											class="w-20 shrink-0"
										/>
										<span class="truncate text-xs text-ink-gray-5">
											{{ paper.stage_label || "starting…" }}
										</span>
									</template>
									<span
										v-else-if="paper.state === 'ready'"
										class="text-xs text-ink-gray-5"
									>
										{{ paper.mapped_questions }} mapped
									</span>
								</div>
								<p
									v-if="paper.state === 'failed'"
									class="mt-1 text-xs text-ink-red-3"
								>
									{{ (paper.error || "").split("\n").pop() }}
								</p>
							</td>
							<td class="p-2">
								<div class="flex justify-end gap-1">
									<a
										v-if="paper.source_pdf"
										:href="paper.source_pdf"
										target="_blank"
										rel="noopener"
									>
										<Button
											size="sm"
											label="PDF"
											icon-left="lucide-file-text"
										/>
									</a>
									<Button
										size="sm"
										label="Re-extract"
										icon-left="lucide-refresh-cw"
										@click="onReextract(paper)"
									/>
									<Button
										size="sm"
										theme="red"
										label="Delete"
										icon-left="lucide-trash-2"
										@click="confirmDelete = paper"
									/>
								</div>
							</td>
						</tr>
					</tbody>
				</table>
			</div>

			<p v-if="errorText" class="rounded bg-surface-red-1 p-3 text-sm text-ink-red-3">
				{{ errorText }}
			</p>

			<p v-else-if="loading" class="py-10 text-sm text-ink-gray-5">Loading…</p>

			<div v-else-if="!topics.length" class="py-10">
				<p class="text-sm text-ink-gray-6">
					No mapped questions for this project yet. Add a paper, wait for extraction,
					then run the mapping.
				</p>
			</div>

			<template v-else>
				<!-- Scope. Everything below is computed against exactly these sources, so the
				     same papers answer two different questions depending on what is selected. -->
				<div
					v-if="availableSources.length"
					class="mb-4 flex flex-wrap items-center gap-2 rounded border border-outline-gray-1 p-3"
				>
					<span class="text-xs font-medium uppercase tracking-wide text-ink-gray-5">
						Study against
					</span>
					<button
						type="button"
						class="rounded-full border px-3 py-1 text-xs transition"
						:class="
							scopedToAll
								? 'border-outline-gray-3 bg-surface-gray-3 text-ink-gray-9'
								: 'border-outline-gray-2 text-ink-gray-6 hover:bg-surface-gray-1'
						"
						@click="setScope([])"
					>
						All sources ({{ availableSources.length }})
					</button>
					<button
						v-for="source in availableSources"
						:key="source.name"
						type="button"
						class="rounded-full border px-3 py-1 text-xs transition"
						:class="
							scope.includes(source.name)
								? 'border-outline-gray-3 bg-surface-gray-3 text-ink-gray-9'
								: 'border-outline-gray-2 text-ink-gray-6 hover:bg-surface-gray-1'
						"
						:title="source.title"
						@click="toggleSource(source.name)"
					>
						{{ (source.title || source.name).slice(0, 34) }}
						<span class="text-ink-gray-5">· {{ source.sections }} sections</span>
					</button>
				</div>

				<div class="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
					<div class="rounded border border-outline-gray-1 p-3">
						<div class="text-xs uppercase tracking-wide text-ink-gray-5">Papers</div>
						<div class="text-xl font-semibold text-ink-gray-9">
							{{ papers.length }}
						</div>
					</div>
					<div class="rounded border border-outline-gray-1 p-3">
						<div class="text-xs uppercase tracking-wide text-ink-gray-5">
							Questions
						</div>
						<div class="text-xl font-semibold text-ink-gray-9">
							{{ totals.questions || 0 }}
						</div>
					</div>
					<div class="rounded border border-outline-gray-1 p-3">
						<div class="text-xs uppercase tracking-wide text-ink-gray-5">
							Attemptable marks
						</div>
						<div class="text-xl font-semibold text-ink-gray-9">
							{{ Math.round(totals.attemptable || 0) }}
						</div>
					</div>
					<div class="rounded border border-outline-gray-1 p-3">
						<div class="text-xs uppercase tracking-wide text-ink-gray-5">Topics</div>
						<div class="text-xl font-semibold text-ink-gray-9">
							{{ totals.topics || 0 }}
						</div>
					</div>
				</div>

				<h2 class="mb-1 text-md font-medium text-ink-gray-9">Covered by your sources</h2>
				<p class="mb-3 text-xs text-ink-gray-5">
					Topics these sources teach, ranked by how heavily they are examined. This is
					what studying the selected material actually prepares you for.
				</p>

				<div class="mb-3 flex items-center justify-between gap-3">
					<p class="text-xs text-ink-gray-5">
						Shading is marks per topic per year. Weighted score applies
						{{ grid?.decay }}<sup>age</sup> decay from {{ grid?.reference_year }}.
					</p>
					<div class="flex shrink-0 gap-1">
						<Button
							:variant="showWeighted ? 'solid' : 'subtle'"
							size="sm"
							@click="showWeighted = true"
						>
							Weighted
						</Button>
						<Button
							:variant="!showWeighted ? 'solid' : 'subtle'"
							size="sm"
							@click="showWeighted = false"
						>
							Raw marks
						</Button>
					</div>
				</div>

				<!-- The grid scrolls inside itself so the page never scrolls sideways. -->
				<div class="overflow-x-auto rounded border border-outline-gray-1">
					<table class="w-full min-w-[40rem] border-collapse text-sm">
						<thead>
							<tr class="border-b border-outline-gray-1 bg-surface-gray-1">
								<th
									class="sticky left-0 z-10 bg-surface-gray-1 p-2 text-left font-medium text-ink-gray-7"
								>
									Topic
								</th>
								<th
									v-for="year in years"
									:key="year"
									class="p-2 text-center font-medium text-ink-gray-7"
								>
									{{ year }}
								</th>
								<th class="p-2 text-right font-medium text-ink-gray-7">
									{{ showWeighted ? "Score" : "Marks" }}
								</th>
							</tr>
						</thead>
						<tbody>
							<tr
								v-for="topic in rankedTopics"
								:key="topic.topic"
								class="border-b border-outline-gray-1 last:border-0"
							>
								<td
									class="sticky left-0 z-10 max-w-[18rem] truncate bg-surface-white p-2 text-ink-gray-8"
									:title="topic.title"
								>
									{{ topic.title }}
									<Badge
										v-if="!topic.evidence_backed"
										theme="orange"
										size="sm"
										class="ml-1"
									>
										similarity only
									</Badge>
								</td>
								<td
									v-for="year in years"
									:key="year"
									class="cursor-pointer p-0 text-center"
									@click="openDrilldown(selected, topic.title, year)"
								>
									<div
										class="m-0.5 rounded py-2 text-xs tabular-nums"
										:class="cellClass(topic.topic, year)"
									>
										{{ cellLabel(topic.topic, year) }}
									</div>
								</td>
								<td
									class="p-2 text-right font-medium tabular-nums text-ink-gray-9"
								>
									{{ showWeighted ? topic.score : Math.round(topic.marks) }}
								</td>
							</tr>
						</tbody>
					</table>
				</div>

				<p class="mt-3 text-xs text-ink-gray-5">
					A topic marked <em>similarity only</em> was matched by wording alone — ICAI’s
					model answer cited no provision we could find in this corpus. Treat those rows
					as weaker evidence than the rest.
				</p>
			</template>
		</div>

		<NewExamPaperDialog
			v-model:open="showUpload"
			:project="selected"
			@uploaded="reloadUntilSettled"
		/>

		<!-- Drill-down. The body scrolls inside a fixed height: a CA question runs to
		     4,000+ characters and without a scroll container the panel overflowed the
		     dialog and the grid behind it showed through the text. -->
		<Dialog
			v-model:open="drillOpen"
			:options="{
				title: drillTopic + (drillYear ? ` · ${drillYear}` : ''),
				size: '4xl',
			}"
		>
			<template #default>
				<div class="max-h-[65vh] overflow-y-auto pr-1">
					<p v-if="drillError" class="text-sm text-ink-red-3">{{ drillError }}</p>
					<p v-else-if="drillLoading" class="text-sm text-ink-gray-5">Loading…</p>

					<template v-else>
						<div v-if="drillSections.length" class="mb-4">
							<h3
								class="text-xs font-medium uppercase tracking-wide text-ink-gray-5"
							>
								Read these sections
							</h3>
							<ul class="mt-1.5 space-y-1">
								<li
									v-for="section in drillSections"
									:key="section.name"
									class="text-sm"
								>
									<a
										v-if="section.pdf"
										:href="`${section.pdf}#page=${section.page_start}`"
										target="_blank"
										rel="noopener"
										class="text-ink-blue-3 hover:underline"
									>
										{{ section.title }}
										<span class="text-ink-gray-5"
											>· p. {{ section.page_start }}</span
										>
									</a>
									<span v-else class="text-ink-gray-7">
										{{ section.title }}
										<span class="text-ink-gray-5"
											>· p. {{ section.page_start }}</span
										>
									</span>
								</li>
							</ul>
						</div>

						<h3 class="text-xs font-medium uppercase tracking-wide text-ink-gray-5">
							{{ drillQuestions.length }} question(s) asked
						</h3>
						<ul class="mt-1.5 space-y-3">
							<li
								v-for="question in drillQuestions"
								:key="question.name"
								class="rounded border border-outline-gray-1 bg-surface-white p-3"
							>
								<div
									class="flex flex-wrap items-center gap-2 text-xs text-ink-gray-6"
								>
									<span class="font-medium text-ink-gray-8">
										Q{{ question.question_no }}
									</span>
									<span>{{ question.exam_year }}</span>
									<Badge size="sm">{{ question.marks }} marks</Badge>
									<Badge size="sm" theme="gray">{{
										question.question_kind
									}}</Badge>
									<Badge v-if="question.is_compulsory" size="sm" theme="blue">
										compulsory
									</Badge>
									<span v-if="question.assessment_year">
										AY {{ question.assessment_year }}
									</span>
									<a
										v-if="papersByName[question.exam_paper]?.source_pdf"
										:href="
											pdfLink(
												papersByName[question.exam_paper],
												question.page_no,
											)
										"
										target="_blank"
										rel="noopener"
										class="text-ink-blue-3 hover:underline"
									>
										open PDF p.{{ question.page_no }}
									</a>
								</div>
								<p
									class="mt-2 whitespace-pre-wrap break-words text-sm text-ink-gray-8"
								>
									{{ question.question_text }}
								</p>
								<p
									v-if="question.statutory_refs"
									class="mt-2 text-xs text-ink-gray-5"
								>
									Cited: {{ question.statutory_refs }}
								</p>
							</li>
						</ul>
					</template>
				</div>
			</template>
		</Dialog>

		<Dialog
			:options="{ title: 'Delete this paper?' }"
			:model-value="!!confirmDelete"
			@update:model-value="confirmDelete = null"
		>
			<template #body-content>
				<p class="text-sm text-ink-gray-7">
					<strong>{{ confirmDelete?.paper_title }}</strong> and its
					{{ confirmDelete?.question_count }} extracted questions will be removed. The
					heatmap recalculates immediately. The uploaded PDF stays in your files.
				</p>
			</template>
			<template #actions>
				<div class="flex justify-end gap-2">
					<Button label="Cancel" @click="confirmDelete = null" />
					<Button
						variant="solid"
						theme="red"
						label="Delete paper"
						@click="onDelete(confirmDelete)"
					/>
				</div>
			</template>
		</Dialog>
	</div>
</template>
