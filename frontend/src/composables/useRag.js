// Data layer for the RAG surfaces (/ask and /rag-lab) — thin wrappers over the
// `wikify.api.rag.*` contract. Every call degrades gracefully: the backend may not be
// deployed yet, so callers get `errorText` instead of an exception.
import { computed, ref, watch } from "vue";
import { useCall, useList } from "frappe-ui";
import { useSocket } from "@/socket";

// `ask` streams over one shared channel and tags every payload with the stream it
// belongs to, unlike the agent loop's per-session `wikify_agent_*:<sid>` events.
const ANSWER_CHANNEL = "wikify_rag_answer";

// Two different identifiers, deliberately named apart. `stream` is a correlation token
// minted here per ask, so this tab can pick its own deltas off a per-user channel; it
// means nothing to the server beyond echoing it back. `session` is a `Wikify Ask Session`
// docname the server mints and we replay, which is what makes a follow-up a follow-up.
// They shared one name until 0.7 — so every ask opened a new conversation, and the
// permission check ran against a docname that had never existed.
function newStreamId() {
	return `rag-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

// Frappe wraps server errors as "PermissionError: You are not allowed to read X" — the
// exception class name is noise to the reader, so only the sentence survives.
function errorMessage(error) {
	if (!error) return "";
	const text = error.messages?.[0] || error.message || String(error);
	if (text === "Failed to fetch") {
		return "Couldn’t reach the server — the request didn’t complete. Try again.";
	}
	return text.replace(/^[A-Za-z]*Error:\s*/, "");
}

// What produced the ordering of a result list, which decides how a hit may be labelled.
// The exhaustive intent runs a metadata filter with no ranking at all — its hits carry a
// constant placeholder score, so they must never be shown as if they were ranked.
const BASIS_FOR_INTENT = { exhaustive: "exhaustive", semantic: "hybrid", hybrid: "hybrid" };

export function relevanceBasis(route) {
	return BASIS_FOR_INTENT[route?.intent] || "hybrid";
}

// A whole result set is unranked when every hit carries the same score: that is the
// exhaustive leg stamping its sentinel, not agreement between hits. Drawn as bars those
// identical numbers fill every bar to the brim and read as certainty no retriever
// claimed — so the set is labelled unranked instead.
export function isUnrankedSet(hits) {
	const scores = (hits || []).map((hit) => hit.score).filter((score) => score != null);
	return scores.length > 1 && scores.every((score) => score === scores[0]);
}

// The project scope selector, shared by /ask and /rag-lab so the two pages can't drift.
// The rows are held once at module scope and the last non-empty list is kept: a reload
// empties `data` for a beat, and with no options to match against, the select falls back
// to its "Select option" placeholder — so the scope the user chose appears to clear itself
// mid-answer.
let projectList = null;
const lastProjectRows = ref([]);
const projectOptions = computed(() => [
	{ label: "All projects", value: "" },
	...lastProjectRows.value.map((row) => ({ label: row.project_name, value: row.name })),
]);

export function useProjectOptions() {
	if (!projectList) {
		projectList = useList({
			doctype: "Wikify Project",
			fields: ["name", "project_name", "is_default"],
			orderBy: "is_default desc, project_name asc",
			limit: 100,
		});
		watch(
			() => projectList.data,
			(rows) => {
				if (rows?.length) lastProjectRows.value = rows;
			},
			{ immediate: true }
		);
	}
	return projectOptions;
}

// Kept at module scope, not per-instance: the layout remounts these pages on a viewport
// breakpoint change, and a remount must not throw away the question, the project scope or
// the results the user is reading.
const question = ref("");
const project = ref("");
const askedQuestion = ref("");
const sources = ref([]);
const answerText = ref("");
const route = ref(null);
const refused = ref(false);
const tookMs = ref(null);
const errorText = ref("");
// Spend on the last answer, and the running total for this browser session. The backend
// may not report cost yet, so these stay null until a payload carries them rather than
// defaulting to a zero that would read as "free".
const usage = ref(null);
const sessionCost = ref(0);
const streaming = ref(false);
const streamId = ref(null);
// The conversation this tab is continuing. Null until the first answer names one, then
// held across asks — clearing it would silently start a new conversation every question.
const conversationId = ref(null);

// A conversation is scoped to the project it was opened against, so switching projects
// starts a new one — otherwise the next follow-up would be rewritten against turns about
// documents the user is no longer looking at.
watch(project, () => {
	conversationId.value = null;
});

// True when the request failed and nothing was retrieved. The page must then show the
// failure alone — empty "Sources 0 / No answer" panels would claim a search happened and
// came back empty.
const failed = computed(
	() => Boolean(errorText.value) && !sources.value.length && !answerText.value
);

// The request itself is module state too, and for a stronger reason than the results:
// `useCall` aborts its in-flight fetch whenever it is re-executed, and a component-scoped
// call is thrown away with the component. An answer takes 10-25 s, long enough for a
// remount (viewport breakpoint, hot reload, a nav there and back) to land mid-flight and
// kill a request the server is still happily answering — which surfaces as a bare
// "Failed to fetch". One app-lifetime call outlives every remount.
const askCall = useCall({
	url: "/api/v2/method/wikify.api.rag.ask",
	method: "POST",
	immediate: false,
});

function handleAnswerEvent(payload) {
	// Strict match: realtime is per-user, not per-tab, so another tab's (or another
	// ask's) answer would otherwise stream into this one.
	if (!payload || payload.stream !== streamId.value) return;
	if (payload.route) route.value = payload.route;
	if (payload.citations) sources.value = payload.citations;
	if (payload.delta) answerText.value += payload.delta;
	if (payload.done) {
		addUsage(payload);
		streaming.value = false;
	}
}

// The `done` event and the HTTP body carry the same fields; whichever arrives first
// records the spend, and the second is ignored so a session total never double-counts.
function addUsage(payload) {
	if (typeof payload?.cost !== "number" || usage.value) return;
	usage.value = {
		cost: payload.cost,
		promptTokens: payload.prompt_tokens ?? null,
		completionTokens: payload.completion_tokens ?? null,
		model: payload.model || "",
	};
	sessionCost.value += payload.cost;
}

function reset() {
	sources.value = [];
	answerText.value = "";
	route.value = null;
	refused.value = false;
	tookMs.value = null;
	errorText.value = "";
	usage.value = null;
}

async function ask() {
	const text = question.value.trim();
	if (!text || streaming.value) return;
	reset();
	askedQuestion.value = text;
	streamId.value = newStreamId();
	streaming.value = true;
	try {
		const response = await askCall.submit({
			question: text,
			project: project.value || null,
			stream: streamId.value,
			session: conversationId.value,
		});
		if (askCall.error) {
			errorText.value = errorMessage(askCall.error);
			return;
		}
		if (!response) return;
		if (response.route) route.value = response.route;
		if (response.citations?.length) sources.value = response.citations;
		// Realtime is best-effort (no replay); the HTTP body is authoritative when
		// no deltas arrived — e.g. socketio down, or the worker finished first.
		if (!answerText.value) answerText.value = response.answer || "";
		// The server owns the conversation's identity: it creates one on the first ask and
		// returns the same name after. Recording it here is what replays history next turn.
		if (response.session) conversationId.value = response.session;
		refused.value = Boolean(response.refused);
		tookMs.value = response.took_ms ?? null;
		addUsage(response);
	} finally {
		streaming.value = false;
	}
}

// Bound once, for the same reason the call is: a remount mid-answer must not unsubscribe
// the stream it is still receiving.
let answerChannelBound = false;

export function useRagAsk() {
	const socket = useSocket();
	if (socket && !answerChannelBound) {
		socket.on(ANSWER_CHANNEL, handleAnswerEvent);
		answerChannelBound = true;
	}

	return {
		question,
		project,
		askedQuestion,
		sources,
		answerText,
		route,
		refused,
		tookMs,
		streaming,
		errorText,
		failed,
		usage,
		sessionCost,
		ask,
		reset,
	};
}

const compareState = {
	query: ref(""),
	project: ref(""),
	comparedQuery: ref(""),
	naive: ref([]),
	routed: ref([]),
	missedSections: ref([]),
	route: ref(null),
	errorText: ref(""),
};

// Module scope for the same reason as `askCall` — a remount must not abort a comparison
// that is still running.
const compareCall = useCall({
	url: "/api/v2/method/wikify.api.rag.compare",
	method: "POST",
	immediate: false,
});

async function compare() {
	const text = compareState.query.value.trim();
	if (!text || compareCall.loading) return;
	compareState.errorText.value = "";
	compareState.comparedQuery.value = text;
	const response = await compareCall.submit({
		query: text,
		project: compareState.project.value || null,
	});
	if (compareCall.error) {
		compareState.errorText.value = errorMessage(compareCall.error);
		// Nothing ran, so the comparison is retracted rather than shown as two empty
		// columns — an empty column reads as "the retriever found nothing".
		compareState.comparedQuery.value = "";
		compareState.naive.value = [];
		compareState.routed.value = [];
		compareState.missedSections.value = [];
		compareState.route.value = null;
		return;
	}
	compareState.naive.value = response?.naive || [];
	compareState.routed.value = response?.routed || [];
	compareState.missedSections.value = response?.missed_by_naive || [];
	compareState.route.value = response?.route || null;
}

export function useRagCompare() {
	return { ...compareState, compare, compareCall };
}

// Module scope again: revisiting the lab should show the counts it already read, not drop
// back to a skeleton while it re-reads them.
const indexState = {
	status: ref(null),
	errorText: ref(""),
	reindexing: ref(false),
	reindexedJob: ref(""),
};

const statusCall = useCall({
	url: "/api/v2/method/wikify.api.rag.index_status",
	method: "GET",
	immediate: false,
});
const reindexCall = useCall({
	url: "/api/v2/method/wikify.api.rag.reindex",
	method: "POST",
	immediate: false,
});

export function useIndexStatus() {
	const { status, errorText, reindexing, reindexedJob } = indexState;

	async function refresh(project) {
		errorText.value = "";
		const response = await statusCall.submit(project ? { project } : {});
		if (statusCall.error) {
			errorText.value = errorMessage(statusCall.error);
			status.value = null;
			return;
		}
		status.value = response;
	}

	async function reindex(project) {
		if (!project || reindexing.value) return;
		reindexing.value = true;
		reindexedJob.value = "";
		errorText.value = "";
		try {
			const response = await reindexCall.submit({ project });
			if (reindexCall.error) {
				errorText.value = errorMessage(reindexCall.error);
				return;
			}
			reindexedJob.value = response?.job || "";
		} finally {
			reindexing.value = false;
		}
	}

	return { status, errorText, reindexing, reindexedJob, refresh, reindex, statusCall };
}
