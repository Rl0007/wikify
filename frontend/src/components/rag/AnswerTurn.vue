<script setup>
// One exchange in the /ask conversation: the question the reader asked, the answer, and
// the evidence folded away behind a disclosure. Sources are collapsed by default — in a
// running conversation the answer is what is being read, and fifteen provenance cards
// between two turns bury the thread. A citation chip opens the disclosure and scrolls to
// the card it names, so the evidence is one click away rather than always in the way.
import { computed, ref, nextTick } from "vue";
import { Button, Spinner } from "frappe-ui";
import MarkdownPreview from "@/components/MarkdownPreview.vue";
import CostMeter from "@/components/rag/CostMeter.vue";
import SourceCard from "@/components/rag/SourceCard.vue";
import { isUnrankedSet, relevanceBasis, turnFailed } from "@/composables/useRag";

const props = defineProps({
	turn: { type: Object, required: true },
	sessionCost: { type: Number, default: null },
});
const emit = defineEmits(["retry"]);

const sourcesOpen = ref(false);
const highlightedSource = ref(0);

const basis = computed(() => relevanceBasis(props.turn.route));
const sourcesUnranked = computed(() => isUnrankedSet(props.turn.sources));
const documentCount = computed(
	() => new Set(props.turn.sources.map((hit) => hit.source_document).filter(Boolean)).size,
);
const failed = computed(() => turnFailed(props.turn));
const elapsed = computed(() => {
	const ms = props.turn.tookMs;
	if (ms == null) return "";
	return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
});

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
	sourcesOpen.value = true;
	await nextTick();
	const card = document.getElementById(`rag-source-${props.turn.id}-${index}`);
	card?.scrollIntoView({ behavior: "smooth", block: "center" });
}

function handleAnswerClick(event) {
	const chip = event.target.closest?.("[data-citation]");
	if (chip) scrollToSource(Number(chip.dataset.citation));
}
</script>

<template>
	<article class="flex flex-col gap-3">
		<div class="flex justify-end">
			<p
				class="max-w-[85%] whitespace-pre-wrap rounded-lg bg-surface-gray-3 px-3.5 py-2 text-base text-ink-gray-9"
			>
				{{ turn.question }}
			</p>
		</div>

		<!-- The request can fail after realtime already delivered the route and sources, so
		     a dropped connection only takes over the turn when nothing arrived at all. -->
		<div
			v-if="failed"
			class="flex flex-col gap-2 rounded-lg border border-outline-red-2 bg-surface-red-1 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
		>
			<div class="min-w-0">
				<p class="text-base font-medium text-ink-gray-9">Nothing was searched</p>
				<p class="text-sm text-ink-gray-7">{{ turn.errorText }}</p>
			</div>
			<Button
				class="shrink-0"
				variant="subtle"
				label="Retry"
				icon-left="lucide-rotate-cw"
				@click="emit('retry')"
			/>
		</div>

		<div
			v-else-if="turn.refused"
			class="rounded-lg border border-outline-amber-1 bg-surface-amber-2 px-4 py-4"
		>
			<div class="flex items-center gap-2">
				<span class="lucide-info size-4 text-ink-amber-8" aria-hidden="true" />
				<p class="text-base font-medium text-ink-gray-9">Not in this wiki</p>
			</div>
			<p class="mt-1.5 text-sm text-ink-gray-7">
				{{
					turn.answer ||
					"The indexed documents don’t cover this. Rather than guess, the answer is withheld — try rephrasing, or widen the project scope."
				}}
			</p>
		</div>

		<div
			v-else-if="turn.rendered"
			class="rag-answer text-base text-ink-gray-9"
			@click="handleAnswerClick"
		>
			<MarkdownPreview :content="turn.rendered" :decorate="addCitationChips" />
		</div>

		<!-- "No answer returned" is only true when the request completed and the model said
		     nothing. If the connection dropped, the answer was lost in transit — saying
		     otherwise sends the reader looking for a gap in the corpus that isn't there. -->
		<div v-else class="text-sm text-ink-gray-5">
			<span v-if="turn.streaming" class="flex items-center gap-2">
				<Spinner class="size-3.5" />
				{{ turn.sources.length ? "Composing the answer…" : "Retrieving sources…" }}
			</span>
			<template v-else-if="turn.errorText">
				<p class="text-ink-gray-7">
					The answer was lost in transit — the sources below it were retrieved before the
					connection dropped.
				</p>
				<Button
					class="mt-2"
					variant="subtle"
					label="Retry"
					icon-left="lucide-rotate-cw"
					@click="emit('retry')"
				/>
			</template>
			<template v-else>No answer returned.</template>
		</div>

		<!-- Provenance, folded. The row doubles as the turn's footer: how long it took and
		     what it cost live here rather than above the answer, where they compete with it. -->
		<div v-if="turn.sources.length" class="flex flex-wrap items-center gap-x-3 gap-y-1">
			<Button
				variant="ghost"
				size="sm"
				:icon-left="sourcesOpen ? 'lucide-chevron-down' : 'lucide-chevron-right'"
				:aria-expanded="sourcesOpen"
				@click="sourcesOpen = !sourcesOpen"
			>
				{{ turn.sources.length }}
				{{ turn.sources.length === 1 ? "source" : "sources" }}
				<span v-if="documentCount" class="ml-1 text-ink-gray-5">
					· {{ documentCount }} {{ documentCount === 1 ? "document" : "documents" }}
				</span>
			</Button>
			<span v-if="elapsed" class="text-xs text-ink-gray-5">{{ elapsed }}</span>
			<div class="flex-1" />
			<CostMeter
				:cost="turn.usage?.cost ?? null"
				:prompt-tokens="turn.usage?.promptTokens ?? null"
				:completion-tokens="turn.usage?.completionTokens ?? null"
				:model="turn.usage?.model || ''"
				:session-cost="sessionCost"
			/>
		</div>

		<div v-if="sourcesOpen" class="flex flex-col gap-2">
			<SourceCard
				v-for="(hit, position) in turn.sources"
				:key="hit.chunk_id || position"
				:hit="hit"
				:index="position + 1"
				:scope="turn.id"
				:total="turn.sources.length"
				:basis="basis"
				:unranked-set="sourcesUnranked"
				:highlighted="highlightedSource === position + 1"
			/>
		</div>
	</article>
</template>
