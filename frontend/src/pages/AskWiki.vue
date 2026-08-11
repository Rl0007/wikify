<script setup>
// /ask — ask a question of the indexed wiki. Sources land before the answer (they are
// published first), the routing decision is shown up front, and citation chips in the
// answer jump to the source they came from.
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import { Button, FormControl, PageHeader } from "frappe-ui";
import MarkdownPreview from "@/components/MarkdownPreview.vue";
import CostMeter from "@/components/rag/CostMeter.vue";
import RouteBadge from "@/components/rag/RouteBadge.vue";
import SourceCard from "@/components/rag/SourceCard.vue";
import { isUnrankedSet, relevanceBasis, useProjectOptions, useRagAsk } from "@/composables/useRag";

const projectOptions = useProjectOptions();

const {
	question,
	project,
	askedQuestion,
	sources,
	answerText,
	route,
	refused,
	tookMs,
	streaming,
	errorText,
	failed,
	usage,
	sessionCost,
	ask,
} = useRagAsk();

const highlightedSource = ref(0);

const basis = computed(() => relevanceBasis(route.value));
const sourcesUnranked = computed(() => isUnrankedSet(sources.value));
const documentCount = computed(
	() => new Set(sources.value.map((hit) => hit.source_document).filter(Boolean)).size,
);
const hasResult = computed(() => Boolean(askedQuestion.value) && !failed.value);
const hasStreamedContent = computed(() => Boolean(sources.value.length || answerText.value));
const elapsed = computed(() =>
	tookMs.value >= 1000 ? `${(tookMs.value / 1000).toFixed(1)} s` : `${tookMs.value} ms`,
);

// The answer streams a token at a time, and every delta would otherwise re-parse the whole
// accumulated markdown. Rendering trails the text by one 50 ms tick — below the cadence a
// reader can see, but it collapses hundreds of full re-parses into a handful.
const RENDER_INTERVAL_MS = 50;
// Seeded from the answer already in module state: a remount mid-read must show it at
// once, not blank to "No answer returned" until the first tick lands.
const renderedText = ref(answerText.value);
let renderTimer = null;

watch(answerText, (text) => {
	if (!text) {
		clearTimeout(renderTimer);
		renderTimer = null;
		renderedText.value = "";
		return;
	}
	if (renderTimer) return;
	renderTimer = setTimeout(() => {
		renderTimer = null;
		renderedText.value = answerText.value;
	}, RENDER_INTERVAL_MS);
});

onUnmounted(() => clearTimeout(renderTimer));

// Citation markers `[1]` become clickable chips that scroll to their source card.
function addCitationChips(rendered) {
	return rendered.replace(
		/\[(\d+)\]/g,
		(match, number) =>
			`<button type="button" class="rag-citation" data-citation="${number}">${number}</button>`,
	);
}

async function scrollToSource(index) {
	highlightedSource.value = index;
	await nextTick();
	const card = document.getElementById(`rag-source-${index}`);
	card?.scrollIntoView({ behavior: "smooth", block: "center" });
}

function handleAnswerClick(event) {
	const chip = event.target.closest?.("[data-citation]");
	if (chip) scrollToSource(Number(chip.dataset.citation));
}
</script>

<template>
	<div class="mx-auto w-full max-w-6xl px-4 pb-16 sm:px-6 sm:pb-28">
		<PageHeader>
			<div class="flex min-w-0 items-center gap-3">
				<h1 class="text-md text-ink-gray-9">Ask</h1>
				<span class="hidden truncate text-sm text-ink-gray-5 lg:inline">
					Answers grounded in your indexed documents
				</span>
			</div>
			<div class="w-32 shrink-0 sm:w-44">
				<FormControl v-model="project" type="select" :options="projectOptions" />
			</div>
		</PageHeader>

		<!-- Question. Sticky so a reader deep in a long source list can ask the next
		     question without scrolling back up, and so the input stays put when the
		     on-screen keyboard shrinks the viewport. -->
		<form class="sticky top-0 z-10 flex gap-2 bg-surface-base py-3" @submit.prevent="ask">
			<FormControl
				v-model="question"
				type="text"
				class="flex-1"
				placeholder="What do you want to know?"
				autocomplete="off"
			/>
			<Button
				class="shrink-0"
				variant="solid"
				label="Ask"
				icon-left="lucide-sparkles"
				:loading="streaming"
				:disabled="!question.trim()"
				@click="ask"
			/>
		</form>

		<!-- The request can fail after realtime already delivered the route and sources, so
		     a dropped connection only takes over the page when nothing arrived at all. -->
		<div
			v-if="failed"
			class="mt-3 flex flex-col gap-2 rounded-md border border-outline-red-2 bg-surface-red-1 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
		>
			<div class="min-w-0">
				<p class="text-base font-medium text-ink-gray-9">Nothing was searched</p>
				<p class="text-sm text-ink-gray-7">{{ errorText }}</p>
			</div>
			<Button
				class="shrink-0"
				variant="subtle"
				label="Retry"
				icon-left="lucide-rotate-cw"
				:loading="streaming"
				@click="ask"
			/>
		</div>
		<p
			v-else-if="errorText"
			class="mt-3 rounded-md bg-surface-amber-2 px-3 py-2 text-sm text-ink-gray-8"
		>
			The connection dropped mid-answer — showing everything that streamed in.
		</p>

		<div v-if="hasResult && (route || streaming)" class="mt-4">
			<RouteBadge :route="route" :loading="streaming && !route" />
		</div>

		<div
			v-if="!hasResult && !failed"
			class="mt-10 flex flex-col items-center gap-3 rounded-lg border border-dashed border-outline-gray-2 px-6 py-16 text-center"
		>
			<span
				class="lucide-message-circle-question size-7 text-ink-gray-4"
				aria-hidden="true"
			/>
			<p class="text-base text-ink-gray-7">Ask a question of this wiki</p>
			<p class="max-w-md text-sm text-ink-gray-5">
				Every answer cites the sections it came from, and shows how the question was routed
				— exhaustive, semantic, or hybrid.
			</p>
		</div>

		<div v-else-if="hasResult" class="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-12">
			<!-- Sources arrive first, so they lead the desktop layout. On a phone the answer
			     goes first: burying it under fifteen source cards makes the reader scroll
			     past the evidence to find what it is evidence for. -->
			<section class="order-2 min-w-0 lg:order-1 lg:col-span-5">
				<div class="mb-2 flex flex-wrap items-center gap-2">
					<h2 class="text-base font-medium text-ink-gray-9">Sources</h2>
					<span
						class="rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs text-ink-gray-7"
					>
						{{ sources.length }}
					</span>
					<span v-if="documentCount" class="text-xs text-ink-gray-5">
						across {{ documentCount }}
						{{ documentCount === 1 ? "document" : "documents" }}
					</span>
				</div>

				<div v-if="sources.length" class="flex flex-col gap-2">
					<SourceCard
						v-for="(hit, position) in sources"
						:key="hit.chunk_id || position"
						:hit="hit"
						:index="position + 1"
						:total="sources.length"
						:basis="basis"
						:unranked-set="sourcesUnranked"
						:highlighted="highlightedSource === position + 1"
					/>
				</div>
				<div
					v-else
					class="rounded-lg border border-dashed border-outline-gray-2 px-4 py-10 text-center text-sm text-ink-gray-5"
				>
					{{ streaming ? "Retrieving sources…" : "No sources matched this question." }}
				</div>
			</section>

			<section class="order-1 min-w-0 lg:order-2 lg:col-span-7">
				<div class="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1">
					<h2 class="text-base font-medium text-ink-gray-9">Answer</h2>
					<span v-if="tookMs != null" class="text-xs text-ink-gray-5">
						{{ elapsed }}
					</span>
					<div class="flex-1" />
					<CostMeter
						:cost="usage?.cost ?? null"
						:prompt-tokens="usage?.promptTokens ?? null"
						:completion-tokens="usage?.completionTokens ?? null"
						:model="usage?.model || ''"
						:session-cost="sessionCost"
					/>
				</div>

				<div
					v-if="refused"
					class="rounded-lg border border-outline-amber-1 bg-surface-amber-2 px-4 py-4"
				>
					<div class="flex items-center gap-2">
						<span class="lucide-info size-4 text-ink-amber-8" aria-hidden="true" />
						<p class="text-base font-medium text-ink-gray-9">Not in this wiki</p>
					</div>
					<p class="mt-1.5 text-sm text-ink-gray-7">
						{{
							answerText ||
							"The indexed documents don’t cover this. Rather than guess, the answer is withheld — try rephrasing, or widen the project scope."
						}}
					</p>
				</div>

				<div
					v-else-if="renderedText"
					class="rag-answer rounded-lg border border-outline-gray-2 bg-surface-elevation-1 px-4 py-4"
					@click="handleAnswerClick"
				>
					<MarkdownPreview :content="renderedText" :decorate="addCitationChips" />
				</div>

				<!-- "No answer returned" is only true when the request completed and the
				     model said nothing. If the connection dropped, the answer was lost in
				     transit — saying otherwise sends the reader looking for a gap in the
				     corpus that isn't there. -->
				<div
					v-else
					class="rounded-lg border border-outline-gray-2 bg-surface-elevation-1 px-4 py-10 text-center text-sm text-ink-gray-5"
				>
					<template v-if="streaming">Composing the answer…</template>
					<template v-else-if="errorText">
						<p class="text-ink-gray-7">
							The answer was lost in transit — the sources beside it were retrieved
							before the connection dropped.
						</p>
						<Button
							class="mt-3"
							variant="subtle"
							label="Retry"
							icon-left="lucide-rotate-cw"
							@click="ask"
						/>
					</template>
					<template v-else>No answer returned.</template>
				</div>
			</section>
		</div>
	</div>
</template>

<style>
/* Citation chips are injected into rendered markdown, so they can't carry scoped
   classes — style them globally against the same tokens the rest of the page uses. */
.rag-citation {
	display: inline-flex;
	align-items: center;
	justify-content: center;
	min-width: 1.15rem;
	height: 1.15rem;
	margin: 0 0.1rem;
	padding: 0 0.25rem;
	border-radius: 0.25rem;
	font-size: 0.7rem;
	font-weight: 600;
	line-height: 1;
	vertical-align: baseline;
	cursor: pointer;
	background-color: var(--surface-blue-2);
	color: var(--ink-blue-8);
}
.rag-citation:hover {
	background-color: var(--surface-blue-3);
}

/* The answer is model-authored markdown: it can contain a rate table, a fenced block or a
   bare URL far wider than a phone. Each of those scrolls inside itself so the page body
   never does. */
.rag-answer {
	overflow-wrap: break-word;
}
.rag-answer pre,
.rag-answer table {
	display: block;
	max-width: 100%;
	overflow-x: auto;
}
</style>
