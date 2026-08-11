<script setup>
// Recall@k per golden question. The eval endpoint is not in the frozen contract yet, so
// this renders an explicitly-labelled placeholder until a later slice wires it up.
import { computed } from "vue";
import CoverageBar from "@/components/rag/CoverageBar.vue";

const props = defineProps({
	rows: { type: Array, default: () => [] },
	loading: { type: Boolean, default: false },
});

const averageRecall = computed(() => {
	if (!props.rows.length) return null;
	const total = props.rows.reduce((sum, row) => sum + (row.recall || 0), 0);
	return total / props.rows.length;
});
</script>

<template>
	<section class="rounded-lg border border-outline-gray-2 bg-surface-elevation-1">
		<header class="flex flex-wrap items-center gap-2 border-b border-outline-gray-2 px-4 py-3">
			<span class="lucide-target size-4 text-ink-gray-5" aria-hidden="true" />
			<h2 class="text-base font-medium text-ink-gray-9">Eval scoreboard</h2>
			<span class="text-xs text-ink-gray-5">recall@k per golden question</span>
			<div class="flex-1" />
			<span v-if="averageRecall != null" class="text-sm text-ink-gray-7">
				avg {{ (averageRecall * 100).toFixed(0) }}%
			</span>
		</header>

		<div v-if="rows.length" class="divide-y divide-outline-gray-1">
			<div
				v-for="row in rows"
				:key="row.question"
				class="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-4"
			>
				<p class="min-w-0 flex-1 truncate text-base text-ink-gray-8">{{ row.question }}</p>
				<div class="w-full sm:w-48">
					<CoverageBar :value="row.recall" :total="1" tone="good" label="recall" />
				</div>
			</div>
		</div>

		<div v-else class="flex flex-col items-center gap-2 px-4 py-10 text-center">
			<span class="lucide-flask-conical size-6 text-ink-gray-4" aria-hidden="true" />
			<p class="text-base text-ink-gray-7">Not wired up yet</p>
			<p class="max-w-md text-sm text-ink-gray-5">
				A later slice adds the golden-question eval endpoint; this board reads it and plots
				recall@k per question — a true proportion, so it gets a real bar.
			</p>
		</div>
	</section>
</template>
