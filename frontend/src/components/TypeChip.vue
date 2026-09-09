<script setup>
// A selectable taxonomy chip: colored dot + label + count. The dot uses the Section
// Type's stored hex `color` (semantic Badge themes don't cover 11 distinct types), so
// it's the one place an inline color is allowed.
defineProps({
	label: { type: String, required: true },
	color: { type: String, default: "#9ca3af" },
	count: { type: Number, default: null },
	active: { type: Boolean, default: false },
});
</script>

<template>
	<button
		type="button"
		class="flex max-w-full min-w-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-sm transition-colors"
		:class="
			active
				? 'border-outline-gray-3 bg-surface-gray-3 text-ink-gray-9'
				: 'border-outline-gray-1 text-ink-gray-7 hover:bg-surface-gray-2'
		"
	>
		<span
			class="size-2 shrink-0 rounded-full"
			:style="{ backgroundColor: color }"
			aria-hidden="true"
		/>
		<span class="min-w-0 truncate">{{ label }}</span>
		<span v-if="count != null" class="shrink-0 text-ink-gray-5">{{ count }}</span>
	</button>
</template>
