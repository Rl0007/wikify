// Data layer for the /ask RAG surface — thin wrappers over the `wikify.api.rag.*`
// contract. Every call degrades gracefully: the backend may not be deployed yet, so
// callers get `errorText` instead of an exception.
import { computed, nextTick, ref, watch } from "vue";
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

// The chosen scope survives a reload — re-picking the project on every visit is the most
// repeated action on this page. Per-browser and per-viewer by nature, so it never travels
// between users; a browser with site data blocked simply doesn't remember.
const PROJECT_STORAGE_KEY = "wikify:ask:project";

function storedProject() {
	try {
		return window.localStorage.getItem(PROJECT_STORAGE_KEY) || "";
	} catch {
		return "";
	}
}

function rememberProject(value) {
	try {
		if (value) window.localStorage.setItem(PROJECT_STORAGE_KEY, value);
		else window.localStorage.removeItem(PROJECT_STORAGE_KEY);
	} catch {
		// Nothing to do: the scope just won't be remembered next time.
	}
}

// The project scope selector for /ask. The rows are held once at module scope and the
// last non-empty list is kept: a reload empties `data` for a beat, and with no options to
// match against, the select falls back to its "Select option" placeholder — so the scope
// the user chose appears to clear itself mid-answer.
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
				if (!rows?.length) return;
				lastProjectRows.value = rows;
				// A remembered project the viewer can no longer see — deleted, or permissions
				// changed — must not stay selected: `ask` refuses an unreadable scope outright,
				// so it would fail every question until they noticed the stale select.
				if (project.value && !rows.some((row) => row.name === project.value)) {
					project.value = "";
				}
			},
			{ immediate: true },
		);
	}
	return projectOptions;
}

// Kept at module scope, not per-instance: the layout remounts these pages on a viewport
// breakpoint change, and a remount must not throw away the draft question, the project
// scope or the transcript the user is reading.
const question = ref("");
const project = ref(storedProject());
// The conversation as the reader sees it: one entry per ask, oldest first. Each turn owns
// its own sources, answer, route and spend, so a follow-up never overwrites the answer
// above it — which is what makes the page a conversation rather than a result view.
const turns = ref([]);
const streaming = ref(false);
// Running spend for this browser session. The backend may not report cost yet, so a turn's
// own usage stays null until a payload carries it rather than defaulting to a zero that
// would read as "free".
const sessionCost = ref(0);
const streamId = ref(null);
// The conversation this tab is continuing. Null until the first answer names one, then
// held across asks — clearing it would silently start a new conversation every question.
const conversationId = ref(null);

// The answer streams a token at a time, and every delta would otherwise re-parse the whole
// accumulated markdown. `rendered` trails `answer` by one 50 ms tick — below the cadence a
// reader can see, but it collapses hundreds of full re-parses into a handful.
const RENDER_INTERVAL_MS = 50;
let renderTimer = null;

// The turn the live stream belongs to. Held as the object itself, not an index: an ask
// that finishes after the reader started the next one must not write into its successor.
// It must be the reactive proxy `turns` hands back, never the object that was pushed in —
// writes to the raw object land in the same data but track nothing, so the answer arrives
// and the transcript keeps showing "No answer returned".
let activeTurn = null;

function newTurn(text) {
	return {
		id: `${Date.now()}-${turns.value.length}`,
		question: text,
		sources: [],
		answer: "",
		rendered: "",
		route: null,
		refused: false,
		tookMs: null,
		usage: null,
		errorText: "",
		streaming: true,
	};
}

// A conversation is scoped to the project it was opened against, so switching projects
// starts a new one — otherwise the next follow-up would be rewritten against turns about
// documents the user is no longer looking at.
watch(project, (value) => {
	rememberProject(value);
	newConversation();
});

function newConversation() {
	conversationId.value = null;
	activeTurn = null;
	clearTimeout(renderTimer);
	renderTimer = null;
	turns.value = [];
	streaming.value = false;
	sessionCost.value = 0;
}

// True when the request failed and nothing was retrieved. The turn must then show the
// failure alone — an empty "no sources / no answer" pair would claim a search happened and
// came back empty.
export function turnFailed(turn) {
	return Boolean(turn.errorText) && !turn.sources.length && !turn.answer;
}

// The request itself is module state too, and for a stronger reason than the transcript:
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

function scheduleRender(turn) {
	if (renderTimer) return;
	renderTimer = setTimeout(() => {
		renderTimer = null;
		turn.rendered = turn.answer;
	}, RENDER_INTERVAL_MS);
}

function handleAnswerEvent(payload) {
	// Strict match: realtime is per-user, not per-tab, so another tab's (or another
	// ask's) answer would otherwise stream into this one.
	if (!payload || !activeTurn || payload.stream !== streamId.value) return;
	const turn = activeTurn;
	if (payload.route) turn.route = payload.route;
	if (payload.citations) turn.sources = payload.citations;
	if (payload.delta) {
		turn.answer += payload.delta;
		scheduleRender(turn);
	}
	if (payload.done) {
		addUsage(turn, payload);
		turn.streaming = false;
		streaming.value = false;
	}
}

// The `done` event and the HTTP body carry the same fields; whichever arrives first
// records the spend, and the second is ignored so a session total never double-counts.
function addUsage(turn, payload) {
	if (typeof payload?.cost !== "number" || turn.usage) return;
	turn.usage = {
		cost: payload.cost,
		promptTokens: payload.prompt_tokens ?? null,
		completionTokens: payload.completion_tokens ?? null,
		model: payload.model || "",
	};
	sessionCost.value += payload.cost;
}

async function ask() {
	const text = question.value.trim();
	if (!text || streaming.value) return;
	turns.value.push(newTurn(text));
	const turn = turns.value.at(-1);
	activeTurn = turn;
	question.value = "";
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
			turn.errorText = errorMessage(askCall.error);
			return;
		}
		if (!response) return;
		if (response.route) turn.route = response.route;
		if (response.citations?.length) turn.sources = response.citations;
		// Realtime is best-effort (no replay); the HTTP body is authoritative when
		// no deltas arrived — e.g. socketio down, or the worker finished first.
		if (!turn.answer) turn.answer = response.answer || "";
		// The server owns the conversation's identity: it creates one on the first ask and
		// returns the same name after. Recording it here is what replays history next turn.
		if (response.session) conversationId.value = response.session;
		turn.refused = Boolean(response.refused);
		turn.tookMs = response.took_ms ?? null;
		addUsage(turn, response);
	} finally {
		clearTimeout(renderTimer);
		renderTimer = null;
		turn.rendered = turn.answer;
		turn.streaming = false;
		streaming.value = false;
	}
}

// Re-run the last question. The failed turn is dropped first so a retry replaces it rather
// than stacking a second copy of the same question in the transcript.
async function retryLastTurn() {
	const turn = turns.value.at(-1);
	if (!turn || streaming.value) return;
	turns.value.pop();
	question.value = turn.question;
	await ask();
}

// ── Conversation history ────────────────────────────────────────────────────────────
// Every ask has been logged since 0.7 (`Wikify Ask Session` + `Wikify Ask Message`, via
// `rag/history.py`); this is the read-back that was never wired up. Module scope like the
// transcript, so reopening the panel shows the list it already read instead of a spinner.
const sessions = ref([]);
const historyError = ref("");

const sessionListCall = useCall({
	url: "/api/v2/method/wikify.api.ask_history.list_sessions",
	method: "GET",
	immediate: false,
});
const sessionGetCall = useCall({
	url: "/api/v2/method/wikify.api.ask_history.get_session",
	method: "GET",
	immediate: false,
});
const sessionDeleteCall = useCall({
	url: "/api/v2/method/wikify.api.ask_history.delete_session",
	method: "POST",
	immediate: false,
});

// A superseded request, not a failure. `useCall` aborts its in-flight fetch whenever it is
// re-executed, and loading a conversation re-runs the list twice in quick succession — the
// scope changes, then the conversation does. Reporting that abort would replace the list
// with an error message while the request that superseded it is still on its way.
function wasAborted(error) {
	return /abort/i.test(error?.name || error?.message || "");
}

// Scoped to the project the reader is asking under, matching the rule that switching
// projects starts a new conversation — history for documents they aren't looking at is
// noise. "All projects" lists every conversation.
async function listSessions() {
	historyError.value = "";
	const response = await sessionListCall.submit(project.value ? { project: project.value } : {});
	if (sessionListCall.error) {
		if (!wasAborted(sessionListCall.error)) {
			historyError.value = errorMessage(sessionListCall.error);
		}
		return;
	}
	sessions.value = response || [];
}

// Stored rows back into the shape the transcript renders. A question and its answer are
// two rows paired by their `turn` integer — creation order is only the fallback for rows
// written before that column existed, because two asks logged inside the same second
// order arbitrarily.
function hydrateTurns(session, messages) {
	const turnsByKey = new Map();
	let questionsSeen = 0;
	for (const row of messages || []) {
		if (row.role === "question") questionsSeen += 1;
		const key = row.turn || questionsSeen || 1;
		let turn = turnsByKey.get(key);
		if (!turn) {
			turn = { ...newTurn(""), id: `${session}-${key}`, streaming: false };
			turnsByKey.set(key, turn);
		}
		if (row.role === "question") turn.question = row.content || "";
		else applyStoredAnswer(turn, row);
	}
	return [...turnsByKey.values()];
}

function applyStoredAnswer(turn, row) {
	turn.answer = row.content || "";
	turn.rendered = turn.answer;
	turn.sources = row.citations || [];
	turn.refused = Boolean(row.refused);
	turn.tookMs = row.took_ms ?? null;
	// The route decides how a hit may be labelled (see `relevanceBasis`), so a row that
	// was never routed stays null rather than becoming an object of three empty fields —
	// which would label an unranked set as hybrid.
	turn.route = row.route_intent
		? {
				intent: row.route_intent,
				section_type: row.route_section_type,
				reason: row.route_reason,
			}
		: null;
	turn.usage =
		typeof row.cost === "number"
			? {
					cost: row.cost,
					promptTokens: row.prompt_tokens ?? null,
					completionTokens: row.completion_tokens ?? null,
					model: row.model || "",
				}
			: null;
}

async function loadSession(name) {
	// A live answer is streaming into `activeTurn`; swapping the transcript under it would
	// leave the rest of that answer writing into an object no longer on screen.
	if (streaming.value) return;
	historyError.value = "";
	const response = await sessionGetCall.submit({ name });
	if (sessionGetCall.error) {
		historyError.value = errorMessage(sessionGetCall.error);
		return;
	}
	if (!response) return;
	// Scope first, transcript second. The `project` watcher clears the transcript, and it
	// runs on the scheduler rather than inline — hydrating before it fires would have the
	// conversation we just restored wiped a tick later.
	if ((response.project || "") !== project.value) {
		project.value = response.project || "";
		await nextTick();
	}
	turns.value = hydrateTurns(response.name, response.messages);
	conversationId.value = response.name;
	// The meter now totals the thread on screen, not what this browser happened to spend.
	sessionCost.value = response.total_cost || 0;
	activeTurn = null;
}

async function deleteSession(name) {
	historyError.value = "";
	await sessionDeleteCall.submit({ name });
	if (sessionDeleteCall.error) {
		historyError.value = errorMessage(sessionDeleteCall.error);
		return;
	}
	sessions.value = sessions.value.filter((row) => row.name !== name);
	// Deleting the open conversation leaves the transcript showing a thread the server no
	// longer has — and the next follow-up would post to a dead docname.
	if (conversationId.value === name) newConversation();
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
		turns,
		streaming,
		sessionCost,
		conversationId,
		ask,
		retryLastTurn,
		newConversation,
		sessions,
		historyError,
		historyLoading: computed(() => sessionListCall.loading),
		listSessions,
		loadSession,
		deleteSession,
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
