<script setup>
import { Button, Progress } from "frappe-ui";

// One row per queued PDF — shared by the New Document dialog and the drop-zone strip on
// the document list, so the two surfaces can't drift. State lives in `usePdfUpload`.
defineProps({
	rows: { type: Array, required: true },
	// Hide the ✕ once the batch is in flight.
	removable: { type: Boolean, default: true },
});
const emit = defineEmits(["remove", "retry"]);
</script>

<template>
	<div class="divide-y divide-outline-gray-1 rounded-md border border-outline-gray-1">
		<div v-for="row in rows" :key="row.id" class="flex items-center gap-3 px-3 py-2">
			<span class="lucide-file-text size-4 shrink-0 text-ink-gray-5" aria-hidden="true" />
			<span class="min-w-0 flex-1 truncate text-sm text-ink-gray-8">{{ row.name }}</span>

			<Progress
				v-if="row.status === 'uploading'"
				:value="row.progress"
				size="sm"
				class="w-20 shrink-0"
			/>
			<span
				v-else-if="row.status === 'uploaded'"
				class="lucide-check size-4 shrink-0 text-ink-green-6"
				aria-hidden="true"
			/>
			<span v-else-if="row.status === 'pending'" class="shrink-0 text-xs text-ink-gray-5">
				Waiting
			</span>
			<template v-else>
				<span class="shrink-0 truncate text-xs text-ink-red-6">{{ row.error }}</span>
				<Button label="Retry" size="sm" @click="emit('retry', row.id)" />
			</template>

			<Button
				v-if="removable && row.status !== 'uploading'"
				variant="ghost"
				size="sm"
				icon="lucide-x"
				tooltip="Remove"
				@click="emit('remove', row.id)"
			/>
		</div>
	</div>
</template>
