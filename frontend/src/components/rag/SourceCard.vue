<script setup>
// One retrieved chunk, shown as provenance: where it came from in the document, why it
// ranked (vector vs full-text rank), and a deep link into the generated wiki.
import { computed } from "vue";
import RelevanceTag from "@/components/rag/RelevanceTag.vue";

const props = defineProps({
	hit: { type: Object, required: true },
	index: { type: Number, required: true },
	total: { type: Number, default: 0 },
	basis: { type: String, default: "hybrid" },
	unrankedSet: { type: Boolean, default: false },
	highlighted: { type: Boolean, default: false },
	missed: { type: Boolean, default: false },
	compact: { type: Boolean, default: false },
});

// The stored chunk is markdown; the card shows a plain-text preview, so heading hashes
// and emphasis markers are stripped rather than rendered.
const snippet = computed(() =>
	(props.hit.text || "")
		.replace(/^#{1,6}\s+/gm, "")
		.replace(/\*\*/g, "")
		.replace(/`/g, "")
		// Rate tables are common in this corpus and their pipes and dash rules read as
		// line noise in a three-line preview, so cells become a readable run of values.
		.replace(/^\s*\|?[\s:|-]{6,}\|?\s*$/gm, "")
		.replace(/[ \t]*\|[ \t]*/g, " · ")
		.replace(/(?:\s*·\s*){2,}/g, " · ")
		.trim(),
);

// The backend zero-fills the provenance block on a citation it never quoted, so 0 means
// "not resolved" here, not page zero — without this the unquoted majority of a set renders
// as a confident "≈ p. 0 · line 0".
function resolved(value) {
	return value != null && value > 0;
}

function formatLineRange(start, end) {
	if (!resolved(start)) return "";
	if (resolved(end) && end !== start) return ` · lines ${start}-${end}`;
	return ` · line ${start}`;
}

// Provenance a student can check against the printed page. `quote_page_no` is the page the
// quote was actually located on, so it earns line numbers with it; an approximate page
// could not be pinned down inside a multi-page section, so it is marked and its lines are
// withheld rather than implying a precision that isn't there. With neither, the card falls
// back to the section's page range and says so.
//
// These fields ride on the HTTP response only — the realtime `citations` event fires
// before the answer exists, so a streamed card simply shows the range until the final
// payload replaces it.
const provenance = computed(() => {
	const hit = props.hit;
	if (resolved(hit.quote_page_no)) {
		const approximate = hit.quote_page_approximate === true;
		const lines = approximate
			? ""
			: formatLineRange(hit.quote_page_line_start, hit.quote_page_line_end);
		return {
			text: `${approximate ? "≈ p. " : "p. "}${hit.quote_page_no}${lines}`,
			approximate,
			title: approximate
				? "Approximate: the page could not be pinned down inside a multi-page section."
				: "Exact page and line resolved in the source document.",
		};
	}
	const start = hit.page_start;
	if (!resolved(start)) return null;
	const end = hit.page_end;
	return {
		text: start === end ? `p. ${start}` : `pp. ${start}-${end}`,
		approximate: start !== end,
		title:
			start === end
				? "Page of the source section."
				: "The section spans these pages; the exact page is not resolved.",
	};
});

// A citation is only as good as its evidence. `unquoted` means the backend pulled no
// quote at all, and that is shown as an absence — the one thing this card must never do is
// let an unchecked quote read as a confirmed one, because a citation that looks confirmed
// while misstating a rate is worse for a student than no citation.
const QUOTE_STATUS = {
	verified: {
		icon: "lucide-check",
		tone: "text-ink-gray-5",
		text: "Quote located in the source page",
	},
	unverified: {
		icon: "lucide-triangle-alert",
		tone: "text-ink-amber-8",
		text: "Not found in the source page — check it before relying on this",
	},
};

const quote = computed(() => (props.hit.quote || "").trim());
const quoteStatus = computed(() => (quote.value ? QUOTE_STATUS[props.hit.quote_status] : null));

const wikiHref = computed(() => {
	if (!props.hit.wiki_route) return "";
	const page = resolved(props.hit.quote_page_no)
		? props.hit.quote_page_no
		: props.hit.page_start;
	return resolved(page) ? `/${props.hit.wiki_route}#page-${page}` : `/${props.hit.wiki_route}`;
});
</script>

<template>
	<article
		:id="`rag-source-${index}`"
		class="flex min-w-0 gap-3 rounded-lg border p-3 transition-colors"
		:class="[
			highlighted
				? 'border-outline-blue-3 bg-surface-blue-2'
				: missed
					? 'border-outline-amber-3 border-l-4 bg-surface-amber-2'
					: 'border-outline-gray-2 bg-surface-elevation-1',
		]"
	>
		<span
			class="flex size-6 shrink-0 items-center justify-center rounded-md text-xs font-semibold tabular-nums"
			:class="
				highlighted
					? 'bg-surface-gray-9 text-ink-gray-1'
					: missed
						? 'border border-outline-amber-3 bg-surface-amber-2 text-ink-gray-9'
						: 'bg-surface-gray-3 text-ink-gray-7'
			"
		>
			{{ index }}
		</span>

		<div class="min-w-0 flex-1">
			<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
				<!-- Section titles in this corpus run long ("Health and Education Cess on
				     income-tax and surcharge"). Clamping to two wrapped lines keeps the
				     title readable on a phone without ever widening the card. -->
				<h3
					class="line-clamp-2 min-w-0 flex-1 text-base font-medium break-words text-ink-gray-9"
				>
					{{ hit.title || hit.document_title || hit.section }}
				</h3>
				<!-- The flag claims a whole line above the title on a phone; sharing one
				     line left the title about ten characters wide. The wrapper carries the
				     line break so the pill itself keeps its natural width. -->
				<div v-if="missed" class="order-first basis-full sm:order-none sm:basis-auto">
					<span
						class="inline-flex shrink-0 items-center gap-1 rounded-full border border-outline-amber-3 px-2 py-0.5 text-xs font-semibold tracking-wide text-ink-gray-9 uppercase"
					>
						<span class="lucide-eye-off size-3 text-ink-amber-8" aria-hidden="true" />
						Naive missed this
					</span>
				</div>
			</div>

			<p class="line-clamp-2 text-xs break-words text-ink-gray-5">
				<span class="text-ink-gray-6">{{ hit.document_title }}</span>
				<span v-if="hit.hierarchy_path"> › {{ hit.hierarchy_path }}</span>
			</p>

			<div class="mt-1.5 flex flex-wrap items-center gap-1.5">
				<span
					v-if="provenance"
					class="rounded px-1.5 py-0.5 font-mono text-xs"
					:class="
						provenance.approximate
							? 'border border-dashed border-outline-gray-3 text-ink-gray-6'
							: 'bg-surface-gray-3 font-medium text-ink-gray-8'
					"
					:title="provenance.title"
				>
					{{ provenance.text }}
				</span>
				<span
					v-if="hit.section_type"
					class="max-w-full min-w-0 truncate rounded bg-surface-gray-2 px-1.5 py-0.5 text-xs text-ink-gray-7"
				>
					{{ hit.section_type }}
				</span>
				<span
					v-if="hit.vector_rank != null"
					class="rounded bg-surface-blue-2 px-1.5 py-0.5 text-xs text-ink-blue-8"
					title="Rank from the vector (meaning) search"
				>
					vec #{{ hit.vector_rank }}
				</span>
				<span
					v-if="hit.fts_rank != null"
					class="rounded bg-surface-green-2 px-1.5 py-0.5 text-xs text-ink-green-8"
					title="Rank from the full-text (keyword) search"
				>
					fts #{{ hit.fts_rank }}
				</span>
				<span
					v-if="hit.rerank_score != null"
					class="rounded bg-surface-violet-2 px-1.5 py-0.5 text-xs text-ink-gray-8"
					title="Score after reranking, 0-10"
				>
					rerank {{ hit.rerank_score.toFixed(1) }}
				</span>
			</div>

			<figure v-if="quote" class="mt-2">
				<blockquote
					class="border-l-2 py-0.5 pl-3 text-sm break-words text-ink-gray-8 italic"
					:class="
						hit.quote_status === 'unverified'
							? 'border-outline-amber-3'
							: 'border-outline-gray-3'
					"
				>
					“{{ quote }}”
				</blockquote>
				<figcaption
					v-if="quoteStatus"
					class="mt-1 flex items-start gap-1 text-xs"
					:class="quoteStatus.tone"
				>
					<span
						:class="[quoteStatus.icon, 'mt-0.5 size-3.5 shrink-0']"
						aria-hidden="true"
					/>
					{{ quoteStatus.text }}
				</figcaption>
			</figure>

			<p
				v-if="!compact && snippet"
				class="mt-2 line-clamp-3 text-sm break-words text-ink-gray-7"
			>
				{{ snippet }}
			</p>

			<div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5">
				<RelevanceTag
					:rank="index"
					:total="total || index"
					:score="hit.score"
					:basis="basis"
					:unranked-set="unrankedSet"
				/>
				<a
					v-if="wikiHref"
					:href="wikiHref"
					target="_blank"
					rel="noopener"
					class="inline-flex shrink-0 items-center gap-1 text-xs text-ink-blue-8 hover:underline"
				>
					<span class="lucide-external-link size-3.5" aria-hidden="true" />
					Open in wiki
				</a>
			</div>
		</div>
	</article>
</template>
