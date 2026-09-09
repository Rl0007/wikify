<script setup>
// /ask — a conversation with the indexed wiki. Turns stack oldest-first above a composer
// pinned to the bottom, so a follow-up is asked where the last answer ended. Evidence is
// folded into each answer (see AnswerTurn) rather than filling a column beside it: in a
// running thread the answer is what is being read, and the sources are what you open when
// you doubt it.
import { computed, nextTick, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Button, FormControl, PageHeader } from "frappe-ui";
import AnswerTurn from "@/components/rag/AnswerTurn.vue";
import AskComposer from "@/components/rag/AskComposer.vue";
import AskHistory from "@/components/rag/AskHistory.vue";
import { useIsNarrow } from "@/composables/useMediaQuery";
import { useProjectOptions, useRagAsk } from "@/composables/useRag";
import { sessionFullName } from "@/data/session";

const projectOptions = useProjectOptions();
const isNarrow = useIsNarrow();

const {
	project,
	turns,
	streaming,
	sessionCost,
	conversationId,
	retryLastTurn,
	newConversation,
	sessions,
	historyError,
	historyLoading,
	listSessions,
	loadSession,
	deleteSession,
} = useRagAsk();

// Whether the rail is open survives a reload, like the project scope: a reader who works
// out of the thread list shouldn't reopen it every visit. Never open by default on narrow
// screens, where it covers the transcript it is meant to sit beside.
const HISTORY_STORAGE_KEY = "wikify:ask:history-open";

function storedHistoryOpen() {
	try {
		return window.localStorage.getItem(HISTORY_STORAGE_KEY) === "true";
	} catch {
		return false;
	}
}

const historyOpen = ref(storedHistoryOpen() && !isNarrow.value);

watch(historyOpen, (open) => {
	try {
		window.localStorage.setItem(HISTORY_STORAGE_KEY, String(open));
	} catch {
		// Nothing to do: the rail just won't remember it was open.
	}
});

// Re-read the list whenever what it should contain changes: the scope filters it, and a
// first ask mints a conversation that belongs at the top of it.
// Immediate, because the rail can come back already open from a previous visit — without
// it that reader waits for an unrelated change before seeing a single conversation.
watch(
	[historyOpen, project, conversationId],
	() => {
		if (historyOpen.value) listSessions();
	},
	{ immediate: true }
);

async function openSession(name) {
	await loadSession(name);
	// On a phone the rail is an overlay over the very transcript it just loaded.
	if (isNarrow.value) historyOpen.value = false;
}

const transcript = ref(null);
const hasTurns = computed(() => turns.value.length > 0);

// Follow the thread as it grows — but only while the reader is still at the bottom of it.
// Scrolling up mid-answer is how you re-read what was already said, and an unconditional
// follow yanks the viewport back down on every delta, making that impossible.
const STICK_THRESHOLD_PX = 48;
const stickToBottom = ref(true);

function handleTranscriptScroll() {
	const element = transcript.value;
	if (!element) return;
	const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
	stickToBottom.value = distanceFromBottom <= STICK_THRESHOLD_PX;
}

async function scrollToLatest() {
	await nextTick();
	const element = transcript.value;
	if (element) element.scrollTop = element.scrollHeight;
}

watch(
	() => turns.value.map((turn) => turn.rendered.length + turn.sources.length).join(),
	() => {
		if (stickToBottom.value) scrollToLatest();
	}
);

// A new turn is the reader's own doing — asking, or opening a conversation from the rail —
// so it always wins the follow back, wherever they had scrolled to before.
watch(
	() => turns.value.length,
	() => {
		stickToBottom.value = true;
		scrollToLatest();
	}
);

// The landing greeting. Read once at setup rather than on a clock: a tab left open past
// midnight showing the evening greeting is a smaller cost than a timer that exists only to
// relabel a heading nobody is looking at.
const firstName = computed(() => (sessionFullName.value || "").trim().split(/\s+/)[0] || "");

function timeOfDayGreeting(now, name) {
	const hour = now.getHours();
	const suffix = name ? `, ${name}` : "";
	if (hour >= 22 || hour < 5) return `Late night session${suffix}`;
	if (hour < 12) return name ? `Hi ${name}` : "Hi";
	if (hour < 17) return `Good afternoon${suffix}`;
	const weekday = now.toLocaleDateString(undefined, { weekday: "long" });
	return `Happy ${weekday}${suffix}`;
}

const greeting = computed(() => timeOfDayGreeting(new Date(), firstName.value));

// The open conversation is route state, not just component state: a refresh, a bookmark or
// a link pasted to a colleague must reopen the same thread. Replace rather than push —
// minting an id is a side-effect of asking, not a step the reader should have to walk back
// through with the back button.
const route = useRoute();
const router = useRouter();

watch(conversationId, (id) => {
	if ((id || "") === (route.params.conversationId || "")) return;
	router.replace({ name: "AskWiki", params: id ? { conversationId: id } : {} });
});

// The other direction: a deep link on load, and the back/forward buttons after it.
// A deep link arrives with no turns loaded, so without this flag the landing — greeting and
// composer — paints for the length of the fetch and is then replaced by the thread. That
// flash reads as "new chat", the opposite of what the link said.
const restoringSession = ref(false);

watch(
	() => route.params.conversationId || "",
	async (id) => {
		if (id === (conversationId.value || "")) return;
		if (!id) {
			newConversation();
			return;
		}
		restoringSession.value = true;
		try {
			await loadSession(id);
		} finally {
			restoringSession.value = false;
		}
	},
	{ immediate: true }
);
</script>

<template>
	<div class="flex h-full flex-col">
		<PageHeader>
			<div class="flex min-w-0 items-center gap-3">
				<h1 class="shrink-0 text-md text-ink-gray-9">Ask</h1>
				<span class="hidden truncate text-sm text-ink-gray-5 lg:inline">
					Answers grounded in your indexed documents
				</span>
			</div>
			<div class="flex shrink-0 items-center gap-2">
				<Button
					v-if="hasTurns"
					variant="ghost"
					icon-left="lucide-plus"
					label="New chat"
					:disabled="streaming"
					@click="newConversation"
				/>
				<div class="w-32 sm:w-44">
					<FormControl v-model="project" type="select" :options="projectOptions" />
				</div>
				<Button
					variant="ghost"
					icon="lucide-history"
					:aria-label="historyOpen ? 'Hide history' : 'Show history'"
					@click="historyOpen = !historyOpen"
				/>
			</div>
		</PageHeader>

		<div class="flex min-h-0 flex-1">
			<div class="flex min-w-0 flex-1 flex-col">
				<!-- Nothing asked yet: the composer is the page, centred under a greeting, the
				     way a conversation starts rather than the way a log ends. The first turn
				     moves it to the bottom (below), where a follow-up belongs. -->
				<div
					v-if="!hasTurns && !restoringSession"
					class="flex min-h-0 flex-1 flex-col items-center justify-center gap-6 px-4 sm:px-6"
				>
					<h2
						class="flex items-center gap-2 text-center text-3xl text-ink-gray-9 sm:text-4xl"
					>
						<!-- The mark's glyph rather than logo.svg: that file bakes a #171717
						     tile behind the same book-open path, which vanishes on a dark page
						     and lands as a heavy black block on a light one. Bare, it takes the
						     theme's ink like the rest of the heading. -->
						<span
							class="lucide-book-open size-9 shrink-0 text-ink-gray-9"
							aria-hidden="true"
						/>
						<!-- leading-none so the line box hugs the glyphs: a greeting with no
						     descenders otherwise sits high in it, and centring on that box
						     leaves the mark looking lifted. -->
						<span class="leading-none">{{ greeting }}</span>
					</h2>

					<AskComposer class="w-full max-w-3xl" :pinned="false" />

					<p class="max-w-lg text-center text-xs text-ink-gray-4">
						Every answer cites the sections it came from. Follow-up questions keep the
						thread.
					</p>
				</div>

				<template v-else>
					<div
						ref="transcript"
						class="min-h-0 flex-1 overflow-y-auto"
						@scroll.passive="handleTranscriptScroll"
					>
						<div
							class="mx-auto flex w-full max-w-3xl flex-col gap-8 px-4 py-6 sm:px-6"
						>
							<!-- Shaped like the turn it is about to become — a question bubble on
							     the right, an answer block under it — so the thread does not jump
							     when the real one arrives. -->
							<div
								v-if="restoringSession && !hasTurns"
								class="flex animate-pulse flex-col gap-3"
								aria-hidden="true"
							>
								<div class="h-8 w-56 self-end rounded-lg bg-surface-gray-3" />
								<div class="mt-2 h-4 w-full rounded bg-surface-gray-2" />
								<div class="h-4 w-11/12 rounded bg-surface-gray-2" />
								<div class="h-4 w-3/4 rounded bg-surface-gray-2" />
								<div class="mt-3 h-4 w-40 rounded bg-surface-gray-2" />
							</div>
							<AnswerTurn
								v-for="turn in turns"
								:key="turn.id"
								:turn="turn"
								:session-cost="sessionCost"
								@retry="retryLastTurn"
							/>
						</div>
					</div>

					<!-- Pinned below the transcript, never scrolling with it: the next question is
					     always reachable, however long the thread above it has grown. It floats as a
					     card in the transcript's own column rather than an edge-to-edge footer, and
					     the bottom padding keeps it off the shell's rounded corner. -->
					<div class="shrink-0 bg-surface-base px-4 pb-4 sm:px-6">
						<AskComposer class="mx-auto w-full max-w-3xl" :pinned="true" />
					</div>
				</template>
			</div>

			<!-- The rail. Wide enough to read a question in two lines; an overlay below the
			     split breakpoint, where a column beside the transcript would leave neither
			     readable. -->
			<div
				v-if="historyOpen && isNarrow"
				class="fixed inset-0 z-10 bg-black/20"
				@click="historyOpen = false"
			/>
			<aside
				v-if="historyOpen"
				class="border-l border-outline-gray-1 bg-surface-base"
				:class="isNarrow ? 'fixed inset-y-0 right-0 z-20 w-72 shadow-lg' : 'w-72 shrink-0'"
			>
				<AskHistory
					:sessions="sessions"
					:loading="historyLoading"
					:error-text="historyError"
					:active-id="conversationId || ''"
					@select="openSession"
					@delete="deleteSession"
					@close="historyOpen = false"
				/>
			</aside>
		</div>
	</div>
</template>

<style>
/* Citation chips are injected into rendered markdown, so they can't carry scoped
   classes — style them globally against the same tokens the rest of the page uses. */
.rag-citation {
	display: inline-flex;
	align-items: center;
	justify-content: center;
	min-width: 1.15rem;
	height: 1.15rem;
	margin: 0 0.1rem;
	padding: 0 0.25rem;
	border-radius: 0.25rem;
	font-size: 0.7rem;
	font-weight: 600;
	line-height: 1;
	vertical-align: baseline;
	cursor: pointer;
	background-color: var(--surface-blue-2);
	color: var(--ink-blue-8);
}
.rag-citation:hover {
	background-color: var(--surface-blue-3);
}

/* The answer is model-authored markdown: it can contain a rate table, a fenced block or a
   bare URL far wider than a phone. Each of those scrolls inside itself so the page body
   never does. */
.rag-answer {
	overflow-wrap: break-word;
}
.rag-answer pre,
.rag-answer table {
	display: block;
	max-width: 100%;
	overflow-x: auto;
}
</style>
