<script setup>
// /rag-lab — the proof surface. Same query through a naive top-k vector search and
// through the router + hybrid retriever, side by side, so the difference is legible
// without reading a log.
import { computed, watch } from "vue";
import { Button, FormControl, PageHeader } from "frappe-ui";
import CoverageBar from "@/components/rag/CoverageBar.vue";
import EvalScoreboard from "@/components/rag/EvalScoreboard.vue";
import IndexStatusCard from "@/components/rag/IndexStatusCard.vue";
import RouteBadge from "@/components/rag/RouteBadge.vue";
import SourceCard from "@/components/rag/SourceCard.vue";
import {
	isUnrankedSet,
	relevanceBasis,
	useIndexStatus,
	useProjectOptions,
	useRagCompare,
} from "@/composables/useRag";

const projectOptions = useProjectOptions();

const {
	query,
	project,
	comparedQuery,
	naive,
	routed,
	missedSections,
	route,
	errorText,
	compare,
	compareCall,
} = useRagCompare();
const {
	status: indexStatusData,
	errorText: indexErrorText,
	reindexing,
	reindexedJob,
	refresh: refreshIndexStatus,
	reindex: reindexProject,
	statusCall,
} = useIndexStatus();

watch(project, (name) => refreshIndexStatus(name || null), { immediate: true });

function sectionKey(hit) {
	return hit.section || hit.chunk_id;
}
function chunkKey(hit) {
	return hit.chunk_id || `${hit.section}-${hit.page_start}`;
}

const naiveSections = computed(() => new Set(naive.value.map(sectionKey)));
// The server names the sections the naive leg missed; only fall back to deriving the
// diff here if an older build doesn't send them.
const missedKeys = computed(() =>
	missedSections.value.length
		? new Set(missedSections.value)
		: new Set(
				routed.value
					.filter((hit) => !naiveSections.value.has(sectionKey(hit)))
					.map(sectionKey),
			),
);
const hasComparison = computed(() => Boolean(comparedQuery.value));

// The naive leg is always a plain vector top-k; the routed leg follows the router, and an
// exhaustive route has no ranking to report at all.
const routedBasis = computed(() => relevanceBasis(route.value));
// The two columns sit side by side, so a bar drawn from a real cosine on the left and a
// bar drawn from a sentinel on the right would invite exactly the comparison neither
// number supports. Each column decides for itself whether its scores mean anything.
const naiveUnranked = computed(() => isUnrankedSet(naive.value));
const routedUnranked = computed(() => isUnrankedSet(routed.value));

function countDistinct(hits, key) {
	return new Set(hits.map((hit) => hit[key]).filter(Boolean)).size;
}
const naiveSummary = computed(() => ({
	sections: naiveSections.value.size,
	documents: countDistinct(naive.value, "source_document"),
}));
const routedSummary = computed(() => ({
	sections: new Set(routed.value.map(sectionKey)).size,
	documents: countDistinct(routed.value, "source_document"),
}));
// The yardstick both columns are measured against: everything either retriever found.
const universe = computed(() => ({
	sections: new Set([...naiveSections.value, ...routed.value.map(sectionKey)]).size,
	documents: countDistinct([...naive.value, ...routed.value], "source_document"),
}));
const missedDocuments = computed(() => {
	const seen = new Set(naive.value.map((hit) => hit.source_document));
	return countDistinct(
		routed.value.filter((hit) => !seen.has(hit.source_document)),
		"source_document",
	);
});
</script>

<template>
	<div class="mx-auto w-full max-w-7xl px-4 pb-16 sm:px-6 sm:pb-28">
		<PageHeader>
			<div class="flex min-w-0 items-center gap-3">
				<h1 class="text-md text-ink-gray-9">RAG Lab</h1>
				<span class="hidden truncate text-sm text-ink-gray-5 lg:inline">
					Naive top-k vector vs routed hybrid, on the same query
				</span>
			</div>
			<div class="w-32 shrink-0 sm:w-44">
				<FormControl v-model="project" type="select" :options="projectOptions" />
			</div>
		</PageHeader>

		<form class="sticky top-0 z-10 flex gap-2 bg-surface-base py-3" @submit.prevent="compare">
			<FormControl
				v-model="query"
				type="text"
				class="flex-1"
				placeholder="Try a query that keyword search alone would miss…"
				autocomplete="off"
			/>
			<Button
				variant="solid"
				label="Compare"
				icon-left="lucide-columns-2"
				class="shrink-0"
				:loading="compareCall.loading"
				:disabled="!query.trim()"
				@click="compare"
			/>
		</form>

		<p
			v-if="errorText"
			class="mt-3 rounded-md bg-surface-red-1 px-3 py-2 text-sm text-ink-red-8"
		>
			{{ errorText }}
		</p>

		<div v-if="hasComparison && route" class="mt-4">
			<RouteBadge :route="route" />
		</div>

		<div
			v-if="!hasComparison"
			class="mt-6 flex flex-col items-center gap-3 rounded-lg border border-dashed border-outline-gray-2 px-6 py-14 text-center"
		>
			<span class="lucide-flask-conical size-7 text-ink-gray-4" aria-hidden="true" />
			<p class="text-base text-ink-gray-7">Run a query to see both retrievers</p>
			<p class="max-w-lg text-sm text-ink-gray-5">
				The left column is a plain top-k vector search. The right column is the routed
				hybrid — anything it surfaced that the naive search never saw is flagged.
			</p>
		</div>

		<template v-else>
			<p v-if="compareCall.loading" class="mt-4 text-sm text-ink-gray-5">
				Running both retrievers over “{{ comparedQuery }}”…
			</p>
			<!-- The recall gap, as loud as it deserves to be — the whole argument of the POC. -->
			<div
				v-else-if="missedKeys.size"
				class="mt-4 flex flex-col gap-3 rounded-lg border border-outline-amber-3 border-l-4 bg-surface-amber-2 px-4 py-4 sm:flex-row sm:items-center sm:gap-5"
			>
				<div class="flex items-center gap-3">
					<span
						class="lucide-eye-off size-6 shrink-0 text-ink-amber-8"
						aria-hidden="true"
					/>
					<span class="text-4xl font-semibold tabular-nums text-ink-gray-9">
						{{ missedKeys.size }}
					</span>
				</div>
				<div class="min-w-0">
					<p class="text-lg font-medium text-ink-gray-9">
						sections naive search never saw
						<template v-if="missedDocuments">
							— across {{ missedDocuments }}
							{{ missedDocuments === 1 ? "document" : "documents" }} it never opened
						</template>
					</p>
					<p class="text-sm text-ink-gray-7">
						Same query, same corpus: “{{ comparedQuery }}”. Naive found
						{{ naiveSummary.sections }} of {{ universe.sections }}; the routed leg
						found {{ routedSummary.sections }}. Every missed section is flagged below.
					</p>
				</div>
			</div>
			<p v-else class="mt-4 text-sm text-ink-gray-6">
				Both retrievers returned the same sections for “{{ comparedQuery }}”.
			</p>

			<!-- On a phone the two columns stack, so they can never be read against each
			     other. This scoreboard carries the comparison instead: both bars on one
			     screen, on the same scale, immediately above the stacked lists. -->
			<div
				v-if="!compareCall.loading"
				class="mt-3 flex flex-col gap-2 rounded-lg border border-outline-gray-2 bg-surface-elevation-1 px-3 py-3 lg:hidden"
			>
				<p class="text-xs font-medium tracking-wide text-ink-gray-5 uppercase">
					Sections found, of {{ universe.sections }}
				</p>
				<div class="flex items-center gap-2">
					<span class="w-14 shrink-0 text-xs text-ink-gray-7">Naive</span>
					<CoverageBar
						class="min-w-0 flex-1"
						:value="naiveSummary.sections"
						:total="universe.sections"
						tone="warn"
						label="sections found by naive search"
					/>
					<span
						class="w-12 shrink-0 text-right font-mono text-xs tabular-nums text-ink-gray-8"
					>
						{{ naiveSummary.sections }}/{{ universe.sections }}
					</span>
				</div>
				<div class="flex items-center gap-2">
					<span class="w-14 shrink-0 text-xs text-ink-gray-7">Routed</span>
					<CoverageBar
						class="min-w-0 flex-1"
						:value="routedSummary.sections"
						:total="universe.sections"
						tone="good"
						label="sections found by routed retrieval"
					/>
					<span
						class="w-12 shrink-0 text-right font-mono text-xs tabular-nums text-ink-gray-8"
					>
						{{ routedSummary.sections }}/{{ universe.sections }}
					</span>
				</div>
			</div>

			<div class="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-2">
				<section class="min-w-0">
					<header
						class="mb-2 flex flex-col gap-1.5 rounded-md bg-surface-gray-2 px-3 py-2"
					>
						<div class="flex flex-wrap items-center gap-2">
							<span class="lucide-brain size-4 text-ink-gray-6" aria-hidden="true" />
							<h2 class="text-base font-medium text-ink-gray-8">
								Naive top-k vector
							</h2>
							<span class="text-xs text-ink-gray-5">
								{{ naiveSummary.sections }} of {{ universe.sections }} sections ·
								{{ naiveSummary.documents }} of {{ universe.documents }} documents
							</span>
						</div>
						<CoverageBar
							class="hidden lg:flex"
							:value="naiveSummary.sections"
							:total="universe.sections"
							tone="warn"
							label="sections found by naive search"
						/>
					</header>
					<div v-if="naive.length" class="flex flex-col gap-2">
						<SourceCard
							v-for="(hit, position) in naive"
							:key="chunkKey(hit)"
							:hit="hit"
							:index="position + 1"
							:total="naive.length"
							basis="vector"
							:unranked-set="naiveUnranked"
							compact
						/>
					</div>
					<p
						v-else
						class="rounded-lg border border-dashed border-outline-gray-2 px-4 py-10 text-center text-sm text-ink-gray-5"
					>
						{{ compareCall.loading ? "Searching…" : "No hits." }}
					</p>
				</section>

				<section class="min-w-0">
					<header
						class="mb-2 flex flex-col gap-1.5 rounded-md bg-surface-green-2 px-3 py-2"
					>
						<div class="flex flex-wrap items-center gap-2">
							<span
								class="lucide-shuffle size-4 text-ink-green-8"
								aria-hidden="true"
							/>
							<h2 class="text-base font-medium text-ink-gray-9">Routed retrieval</h2>
							<span class="text-xs text-ink-gray-7">
								{{ routedSummary.sections }} of {{ universe.sections }} sections ·
								{{ routedSummary.documents }} of {{ universe.documents }} documents
							</span>
						</div>
						<CoverageBar
							class="hidden lg:flex"
							:value="routedSummary.sections"
							:total="universe.sections"
							tone="good"
							label="sections found by routed retrieval"
						/>
					</header>
					<div v-if="routed.length" class="flex flex-col gap-2">
						<SourceCard
							v-for="(hit, position) in routed"
							:key="chunkKey(hit)"
							:hit="hit"
							:index="position + 1"
							:total="routed.length"
							:basis="routedBasis"
							:unranked-set="routedUnranked"
							:missed="missedKeys.has(sectionKey(hit))"
							compact
						/>
					</div>
					<p
						v-else
						class="rounded-lg border border-dashed border-outline-gray-2 px-4 py-10 text-center text-sm text-ink-gray-5"
					>
						{{ compareCall.loading ? "Searching…" : "No hits." }}
					</p>
				</section>
			</div>
		</template>

		<div class="mt-6 grid min-w-0 grid-cols-1 gap-4 xl:grid-cols-2">
			<IndexStatusCard
				:status="indexStatusData"
				:loading="statusCall.loading"
				:error-text="indexErrorText"
				:reindexing="reindexing"
				:job-name="reindexedJob"
				:can-reindex="Boolean(project)"
				@reindex="reindexProject(project)"
			/>
			<EvalScoreboard />
		</div>
	</div>
</template>
