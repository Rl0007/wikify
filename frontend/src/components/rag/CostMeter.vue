<script setup>
// What the last answer cost, and what the session has spent so far. A running meter, not
// a headline: quiet type, no colour, and it disappears entirely until the backend sends
// the numbers — "$0.00" would read as free when the truth is "not measured yet".
import { computed } from "vue";

const props = defineProps({
	cost: { type: Number, default: null },
	promptTokens: { type: Number, default: null },
	completionTokens: { type: Number, default: null },
	model: { type: String, default: "" },
	sessionCost: { type: Number, default: null },
});

// Four decimals: a single answer costs fractions of a cent, and rounding to two would
// print $0.00 for every real charge.
function formatCost(amount) {
	return `$${amount.toFixed(4)}`;
}

const tokens = computed(() => (props.promptTokens || 0) + (props.completionTokens || 0));
const hasCost = computed(() => typeof props.cost === "number" && Number.isFinite(props.cost));
const showSessionTotal = computed(
	() =>
		typeof props.sessionCost === "number" &&
		Number.isFinite(props.sessionCost) &&
		props.sessionCost > (props.cost || 0)
);
</script>

<template>
	<p v-if="hasCost" class="flex flex-wrap items-center gap-x-1.5 text-xs text-ink-gray-5">
		<span
			class="font-mono tabular-nums"
			:title="
				promptTokens != null
					? `${promptTokens} prompt + ${completionTokens} completion tokens`
					: 'Cost of this answer'
			"
		>
			{{ formatCost(cost) }}
		</span>
		<template v-if="tokens">
			<span aria-hidden="true">·</span>
			<span class="tabular-nums">{{ tokens.toLocaleString() }} tokens</span>
		</template>
		<template v-if="model">
			<span aria-hidden="true">·</span>
			<span class="truncate">{{ model }}</span>
		</template>
		<template v-if="showSessionTotal">
			<span aria-hidden="true">·</span>
			<span class="tabular-nums" title="Total spent in this session">
				{{ formatCost(sessionCost) }} this session
			</span>
		</template>
	</p>
</template>
