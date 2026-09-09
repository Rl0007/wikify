<script setup>
// The /ask composer. It renders in two places — centred under the greeting on an empty
// thread, pinned to the bottom once turns exist — so it lives here rather than being
// written twice. useRagAsk's state is module-scoped, so this shares the page's refs
// instead of plumbing them through props.
import { Button, Textarea } from "frappe-ui";
import { useRagAsk } from "@/composables/useRag";

// Placement comes from the page, which is the only thing that knows it. Inferring it from
// the turn count instead gets a restored conversation wrong: its turns arrive after the
// fetch, so the composer would wear its landing face under a thread that is already there.
// On the landing the input is the whole invitation and Enter sends it, so a button beside
// it is one more thing to read; pinned under a thread it earns its place as the send
// affordance.
defineProps({
	pinned: { type: Boolean, default: false },
});

const { question, streaming, ask } = useRagAsk();

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
			:placeholder="pinned ? 'Ask a follow-up…' : 'Ask a question of this wiki…'"
			@keydown="handleKeydown"
		/>
		<Button
			v-if="pinned"
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
