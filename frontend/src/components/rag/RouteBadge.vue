<script setup>
// The routing decision made visible — the product thesis, not a footnote. Shows the
// chosen intent, any section-type filter it applied, and the router's reason in words.
import { computed } from "vue";

const props = defineProps({
	route: { type: Object, default: null },
	loading: { type: Boolean, default: false },
});

const INTENTS = {
	exhaustive: {
		label: "Exhaustive",
		icon: "lucide-list-checks",
		blurb: "Returns every matching section, not just the top few.",
		chip: "bg-surface-violet-2 text-ink-violet-8",
		accent: "text-ink-violet-8",
	},
	semantic: {
		label: "Semantic",
		icon: "lucide-brain",
		blurb: "Ranks by meaning — closest passages win.",
		chip: "bg-surface-blue-2 text-ink-blue-8",
		accent: "text-ink-blue-8",
	},
	hybrid: {
		label: "Hybrid",
		icon: "lucide-shuffle",
		blurb: "Blends keyword and meaning, fused by reciprocal rank.",
		chip: "bg-surface-green-2 text-ink-green-8",
		accent: "text-ink-green-8",
	},
};

const intent = computed(() => INTENTS[props.route?.intent] || null);
</script>

<template>
	<div
		v-if="loading"
		class="flex items-center gap-3 rounded-lg border border-outline-gray-2 bg-surface-gray-1 px-4 py-3"
	>
		<div class="h-7 w-28 animate-pulse rounded-full bg-surface-gray-3" />
		<div class="h-3 w-56 animate-pulse rounded bg-surface-gray-3" />
	</div>

	<div
		v-else-if="intent"
		class="rounded-lg border border-outline-gray-2 bg-surface-gray-1 px-4 py-3"
	>
		<!-- Side by side only from `lg`. At tablet widths the chips row is unshrinkable, so
		     a two-column split left the reason paragraph one word wide and spilled it out
		     of the card. -->
		<div class="flex flex-col gap-2 lg:flex-row lg:items-start lg:gap-4">
			<div class="flex w-full min-w-0 flex-wrap items-center gap-2 lg:w-auto">
				<span class="text-xs font-medium tracking-wide text-ink-gray-5 uppercase">
					Route
				</span>
				<span
					class="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-semibold"
					:class="intent.chip"
				>
					<span :class="[intent.icon, 'size-4', intent.accent]" aria-hidden="true" />
					{{ intent.label }}
				</span>
				<span
					v-if="route.section_type"
					class="inline-flex min-w-0 max-w-full basis-full items-center gap-1.5 rounded-full border border-outline-gray-2 bg-surface-elevation-1 px-2.5 py-1 text-sm text-ink-gray-7 lg:basis-auto"
				>
					<span
						class="lucide-filter size-3.5 shrink-0 text-ink-gray-5"
						aria-hidden="true"
					/>
					<!-- Without `min-w-0` this flex item refuses to shrink, so a long
					     section_type pushes the chip past the card edge instead of
					     truncating inside it. -->
					<span class="min-w-0 truncate">{{ route.section_type }}</span>
				</span>
			</div>
			<div class="min-w-0 flex-1">
				<p class="text-base text-ink-gray-8">{{ route.reason || intent.blurb }}</p>
				<p v-if="route.query" class="line-clamp-1 text-xs break-all text-ink-gray-5">
					Searched as “{{ route.query }}”
				</p>
			</div>
		</div>
	</div>
</template>
