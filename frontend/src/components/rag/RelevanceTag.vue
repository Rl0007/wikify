<script setup>
// How a hit earned its place, stated honestly.
//
// There used to be a filled "score" bar here. It lied twice: the exhaustive (`filter`)
// leg has no ranking at all, so every hit carried the sentinel score 1.0 and every bar
// drew full width; and the fused legs score with reciprocal-rank fusion (~0.031 for
// rank 1, ~0.030 for rank 5), which is rank-derived, unbounded-below and not comparable
// across queries — normalising it against the best hit paints near-full bars for
// everything. So a bar survives only on the plain vector leg, where the number is an
// absolute 0-1 similarity that can sit on a 0-1 axis; everywhere else the honest claim is
// position in this list, or nothing at all.
import { computed } from "vue";
import CoverageBar from "@/components/rag/CoverageBar.vue";

const props = defineProps({
	rank: { type: Number, default: null },
	total: { type: Number, default: 0 },
	score: { type: Number, default: null },
	basis: { type: String, default: "hybrid" }, // exhaustive | hybrid | vector
	// Set when every hit in the list shares one score, whatever the route said it was.
	unrankedSet: { type: Boolean, default: false },
});

const BASES = {
	exhaustive: {
		label: "exhaustive match — unranked",
		icon: "lucide-list-checks",
		chip: "bg-surface-violet-2 text-ink-violet-8",
		hint: "This query used an exhaustive metadata filter: every matching section is returned, so there is no ranking to show.",
	},
	hybrid: {
		label: "fused rank",
		icon: "lucide-shuffle",
		chip: "bg-surface-gray-3 text-ink-gray-8",
		hint: "Position after reciprocal-rank fusion of the keyword and meaning searches. A rank, not a confidence — not comparable between queries.",
	},
	vector: {
		label: "meaning rank",
		icon: "lucide-brain",
		chip: "bg-surface-gray-3 text-ink-gray-8",
		hint: "Position by embedding similarity to the question. A rank within these results, not a confidence score.",
	},
};

const basis = computed(() => BASES[props.basis] || BASES.hybrid);
const unranked = computed(
	() => props.basis === "exhaustive" || props.unrankedSet || props.rank == null,
);
// Only the vector leg produces a value on a real 0-1 axis, so only it earns a bar.
const showSimilarity = computed(
	() => props.basis === "vector" && !unranked.value && props.score != null,
);
const hint = computed(() =>
	props.score == null || unranked.value
		? basis.value.hint
		: `${basis.value.hint} Raw score ${props.score.toFixed(4)}.`,
);
</script>

<template>
	<span
		v-if="unranked"
		class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium"
		:class="BASES.exhaustive.chip"
		:title="BASES.exhaustive.hint"
	>
		<span :class="[BASES.exhaustive.icon, 'size-3.5']" aria-hidden="true" />
		{{ BASES.exhaustive.label }}
	</span>

	<span v-else class="inline-flex min-w-0 flex-1 items-center gap-2 text-xs" :title="hint">
		<span
			class="inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 font-mono font-medium tabular-nums"
			:class="basis.chip"
		>
			<span :class="[basis.icon, 'size-3.5']" aria-hidden="true" />
			#{{ rank }}
		</span>
		<CoverageBar
			v-if="showSimilarity"
			class="min-w-16 flex-1"
			:value="score"
			:total="1"
			label="similarity to the question"
		/>
		<span v-else class="text-ink-gray-5">of {{ total }} by {{ basis.label }}</span>
	</span>
</template>
