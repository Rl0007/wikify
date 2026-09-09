<script setup>
// The /ask conversation list, as a rail beside the transcript rather than a dropdown:
// these titles are whole questions, and picking a thread back up means reading them, not
// recognising one from six truncated characters.
import { Button, Spinner, dayjs } from "frappe-ui";

defineProps({
	sessions: { type: Array, default: () => [] },
	loading: { type: Boolean, default: false },
	errorText: { type: String, default: "" },
	activeId: { type: String, default: "" },
});
defineEmits(["select", "delete", "close"]);

function askedAt(row) {
	return row.modified ? dayjs(row.modified).format("D MMM, h:mm a") : "";
}

// The conversation's own count, which counts question and answer rows alike — halved so
// the rail says "3 questions", not the "6 messages" a reader would read as six of theirs.
function questionCount(row) {
	return Math.max(1, Math.round((row.message_count || 0) / 2));
}
</script>

<template>
	<div class="flex h-full flex-col">
		<div class="flex shrink-0 items-center justify-between gap-2 px-3 py-3">
			<h2 class="text-sm text-ink-gray-8">History</h2>
			<Button
				variant="ghost"
				icon="lucide-x"
				aria-label="Close history"
				@click="$emit('close')"
			/>
		</div>

		<div class="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
			<p v-if="errorText" class="px-1 py-2 text-sm text-ink-red-4">{{ errorText }}</p>

			<div v-else-if="loading && !sessions.length" class="flex justify-center py-6">
				<Spinner class="size-4" />
			</div>

			<p v-else-if="!sessions.length" class="px-1 py-2 text-sm text-ink-gray-5">
				No conversations yet in this scope. Every question you ask is kept here.
			</p>

			<ul v-else class="flex flex-col gap-0.5">
				<li
					v-for="row in sessions"
					:key="row.name"
					class="flex items-start gap-1 rounded p-1 hover:bg-surface-gray-2"
					:class="row.name === activeId && 'bg-surface-gray-3'"
				>
					<button
						type="button"
						class="min-w-0 flex-1 rounded px-1 py-1 text-left"
						@click="$emit('select', row.name)"
					>
						<span class="line-clamp-2 text-sm text-ink-gray-8">
							{{ row.title || "Untitled conversation" }}
						</span>
						<span class="mt-0.5 block truncate text-xs text-ink-gray-5">
							{{ askedAt(row) }} · {{ questionCount(row) }}
							{{ questionCount(row) === 1 ? "question" : "questions" }}
						</span>
					</button>
					<Button
						variant="ghost"
						icon="lucide-trash-2"
						:aria-label="`Delete conversation: ${row.title || row.name}`"
						@click="$emit('delete', row.name)"
					/>
				</li>
			</ul>
		</div>
	</div>
</template>
