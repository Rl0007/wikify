<script setup>
// The /ask composer. It renders in two places — centred under the greeting on an empty
// thread, pinned to the bottom once turns exist — so it lives here rather than being
// written twice. useRagAsk's state is module-scoped, so this shares the page's refs
// instead of plumbing them through props.
import { computed } from "vue";
import { Button, Textarea } from "frappe-ui";
import { useRagAsk } from "@/composables/useRag";

const { question, turns, streaming, ask } = useRagAsk();

// The same condition that decides where the page puts this composer. On the landing the
// input is the whole invitation and Enter sends it, so a button beside it is one more
// thing to read; once the thread is running it earns its place as the send affordance.
const pinnedToBottom = computed(() => turns.value.length > 0);

// Enter sends, Shift+Enter breaks the line — the composer is a textarea so a long question
// can be composed, but the common case is one line and a send.
function handleKeydown(event) {
	if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
	event.preventDefault();
	ask();
}
</script>

<template>
	<form
		class="flex items-end gap-2 rounded-xl border border-outline-gray-2 bg-surface-base p-1.5 shadow-sm transition-shadow focus-within:border-outline-gray-4 focus-within:shadow-md"
		@submit.prevent="ask"
	>
		<!-- Ghost ships no background of its own, so without bg-transparent the textarea
		     falls back to the browser default and renders white in dark mode. -->
		<Textarea
			v-model="question"
			:rows="1"
			variant="ghost"
			class="max-h-40 flex-1 resize-none bg-transparent"
			:placeholder="pinnedToBottom ? 'Ask a follow-up…' : 'Ask a question of this wiki…'"
			@keydown="handleKeydown"
		/>
		<Button
			v-if="pinnedToBottom"
			class="shrink-0"
			variant="solid"
			icon="lucide-arrow-up"
			aria-label="Ask"
			:loading="streaming"
			:disabled="!question.trim()"
			@click="ask"
		/>
	</form>
</template>
