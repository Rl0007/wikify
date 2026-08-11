<script setup>
// A bar for the one thing a bar can honestly say here: a share of a known whole —
// sections covered out of the sections that exist, or recall out of 1. Retrieval scores
// are NOT proportions and must never be drawn with this.
import { computed } from "vue";

const props = defineProps({
	value: { type: Number, default: 0 },
	total: { type: Number, default: 1 },
	label: { type: String, default: "coverage" },
	tone: { type: String, default: "neutral" }, // neutral | good | warn
});

const TONES = {
	neutral: "bg-surface-gray-8",
	good: "bg-surface-green-7",
	warn: "bg-surface-amber-7",
};

const percent = computed(() => {
	const total = props.total > 0 ? props.total : 1;
	const ratio = Math.max(0, Math.min(1, (props.value || 0) / total));
	return Math.round(ratio * 100);
});

// A data-driven width has no static Tailwind equivalent; the bound style is confined to
// this one dimension and every colour still comes from a semantic token.
const fillStyle = computed(() => ({ width: `${percent.value}%` }));
</script>

<template>
	<div class="flex items-center gap-2">
		<div class="h-2 min-w-16 flex-1 overflow-hidden rounded-full bg-surface-gray-3">
			<div
				class="h-full rounded-full transition-all"
				:class="TONES[tone] || TONES.neutral"
				:style="fillStyle"
				role="meter"
				:aria-label="label"
				:aria-valuenow="percent"
				aria-valuemin="0"
				aria-valuemax="100"
			/>
		</div>
		<span class="shrink-0 font-mono text-xs tabular-nums text-ink-gray-6">{{ percent }}%</span>
	</div>
</template>
