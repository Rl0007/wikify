<script setup>
// Floating chat window for the Wikify AI agent (0.4 slice 24 — was a fixed slide-over).
// Slice 12 shipped the message list + tool-call cards + streaming bubble + input.
// Slice 13 added the context chips row + a session-history dropdown. Slice 16 added a
// model picker, session rename/archive, error retry, and empty states. The window is
// draggable by its header, resizable from the corner, minimizes to a pill, and
// remembers its geometry; under 640px it falls back to a full-screen sheet.
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { Button, dialog, Dropdown, Spinner, Textarea } from "frappe-ui";
import MarkdownPreview from "@/components/MarkdownPreview.vue";
import { useAgentChat } from "@/composables/useAgentChat";

const props = defineProps({ open: { type: Boolean, default: false } });
const emit = defineEmits(["update:open"]);

// --- floating-window geometry -----------------------------------------------------------

const GEO_KEY = "wikify:agentWindow";
const DEFAULT_SIZE = { w: 416, h: Math.round(window.innerHeight * 0.65) };

const phoneQuery = window.matchMedia("(max-width: 639px)");
const isPhone = ref(phoneQuery.matches);
const onPhoneChange = (e) => (isPhone.value = e.matches);
phoneQuery.addEventListener("change", onPhoneChange);
onBeforeUnmount(() => phoneQuery.removeEventListener("change", onPhoneChange));

function clamped(geo) {
	const w = Math.min(geo.w || DEFAULT_SIZE.w, window.innerWidth - 16);
	const h = Math.min(geo.h || DEFAULT_SIZE.h, window.innerHeight - 16);
	// Keep at least the header reachable so the window can't restore off-screen.
	const x = Math.min(Math.max(geo.x ?? window.innerWidth - w - 24, 8), window.innerWidth - 80);
	const y = Math.min(Math.max(geo.y ?? window.innerHeight - h - 24, 8), window.innerHeight - 80);
	return { x, y, w, h };
}

const geo = reactive(clamped(JSON.parse(localStorage.getItem(GEO_KEY) || "{}")));
function saveGeo() {
	localStorage.setItem(GEO_KEY, JSON.stringify({ ...geo }));
}

const windowStyle = computed(() =>
	isPhone.value
		? {}
		: { left: `${geo.x}px`, top: `${geo.y}px`, width: `${geo.w}px`, height: `${geo.h}px` }
);

const minimized = ref(false);

// Drag by the header. Buttons inside the header still work — drag only starts on the
// header surface itself (pointerdown on a button never reaches threshold behavior).
let dragFrom = null;
function startDrag(e) {
	if (isPhone.value || e.target.closest("button")) return;
	dragFrom = { px: e.clientX, py: e.clientY, x: geo.x, y: geo.y };
	window.addEventListener("pointermove", onDrag);
	window.addEventListener("pointerup", endDrag);
}
function onDrag(e) {
	if (!dragFrom) return;
	const next = clamped({
		...geo,
		x: dragFrom.x + (e.clientX - dragFrom.px),
		y: dragFrom.y + (e.clientY - dragFrom.py),
	});
	geo.x = next.x;
	geo.y = next.y;
}
function endDrag() {
	dragFrom = null;
	window.removeEventListener("pointermove", onDrag);
	window.removeEventListener("pointerup", endDrag);
	saveGeo();
}
onBeforeUnmount(endDrag);

// CSS `resize` supplies the corner handle; observe the element to persist the size.
// Observation starts only after the enter transition settles — the observer would
// otherwise capture the mid-animation (min-size) box and persist a shrunken window.
const windowEl = ref(null);
let resizeObserver = null;
let observeTimer = null;
onMounted(() => {
	resizeObserver = new ResizeObserver(([entry]) => {
		if (isPhone.value || !entry || dragFrom) return;
		const { width, height } = entry.contentRect;
		if (width && height && (Math.abs(width - geo.w) > 2 || Math.abs(height - geo.h) > 2)) {
			geo.w = Math.round(width);
			geo.h = Math.round(height);
			saveGeo();
		}
	});
});
watch(windowEl, (el) => {
	resizeObserver?.disconnect();
	clearTimeout(observeTimer);
	if (el) observeTimer = setTimeout(() => resizeObserver?.observe(el), 300);
});
onBeforeUnmount(() => {
	clearTimeout(observeTimer);
	resizeObserver?.disconnect();
});

const chat = useAgentChat();
const { messages, prompt, isRunning, errorText, attachments, sessionId, model, models } = chat;

const TOOL_STATUS_LABEL = {
	running: "running…",
	needs_confirmation: "needs confirmation",
	done: "done",
};

const CHIP_ICON = {
	project: "lucide-folder",
	document: "lucide-file-text",
	page: "lucide-file",
	section: "lucide-list-tree",
};

const shortModel = (m) => (m || "").split("/").pop() || "Default";

// Session-history dropdown — fetched lazily when opened. Titles are often whole
// first prompts; clamp them so the menu stays a sane width next to the window.
const clamp = (s, n = 48) => (s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s);
const sessionOptions = computed(() => {
	const list = (chat.sessions.value || []).map((s) => ({
		label: clamp(s.title || "Untitled chat"),
		onClick: () => chat.loadSession(s.name),
	}));
	return list.length
		? list
		: [{ label: "No saved chats yet", onClick: () => {}, disabled: true }];
});
async function refreshSessions() {
	await chat.listSessions();
}

// Model picker — populated from get_agent_models when the panel first opens.
const modelOptions = computed(() =>
	(models.value || []).map((m) => ({ label: shortModel(m), onClick: () => (model.value = m) }))
);
watch(
	() => props.open,
	(open) => {
		if (open) chat.loadModels();
	},
	{ immediate: true }
);

// Rename dialog.
function openRename() {
	const s = (chat.sessions.value || []).find((x) => x.name === sessionId.value);
	dialog.prompt({
		title: "Rename chat",
		confirmLabel: "Save",
		fields: [
			{
				name: "title",
				label: "Chat title",
				placeholder: "e.g. Nephrology tree cleanup",
				defaultValue: s?.title || "",
				required: true,
			},
		],
		async onConfirm({ values }) {
			await chat.renameSession(values.title);
		},
	});
}

// Show a Retry affordance when the last message is an agent error.
const showRetry = computed(() => messages.value.at(-1)?.status === "error");

const listEl = ref(null);
async function scrollToBottom() {
	await nextTick();
	if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight;
}
watch(() => messages.value.length, scrollToBottom);
watch(() => messages.value.map((m) => m.content).join("").length, scrollToBottom);

function onSubmit() {
	chat.submitPrompt();
}

function onKeydown(e) {
	if (e.key === "Enter" && !e.shiftKey) {
		e.preventDefault();
		onSubmit();
	}
}
</script>

<template>
	<!-- Minimized pill — pulses while a run streams so background work stays visible. -->
	<button
		v-if="props.open && minimized"
		class="fixed bottom-6 right-6 z-40 flex items-center gap-2 rounded-full border border-outline-gray-2 bg-surface-base px-4 py-2 shadow-lg hover:bg-surface-gray-2"
		aria-label="Restore assistant"
		@click="minimized = false"
	>
		<span
			class="lucide-sparkles size-4 text-ink-gray-7"
			:class="isRunning ? 'animate-pulse' : ''"
			aria-hidden="true"
		/>
		<span class="text-sm font-medium text-ink-gray-8">Assistant</span>
		<Spinner v-if="isRunning" class="size-3.5" />
	</button>

	<Transition
		enter-active-class="transition duration-150 ease-out"
		leave-active-class="transition duration-100 ease-in"
		enter-from-class="scale-95 opacity-0"
		leave-to-class="scale-95 opacity-0"
	>
		<aside
			v-if="props.open && !minimized"
			ref="windowEl"
			class="fixed z-40 flex origin-bottom-right flex-col border border-outline-gray-1 bg-surface-base shadow-2xl"
			:class="isPhone ? 'inset-0' : 'min-h-80 min-w-80 resize overflow-hidden rounded-lg'"
			:style="windowStyle"
		>
			<header
				class="flex min-h-12 items-center justify-between gap-2 border-b border-outline-gray-1 px-3"
				:class="isPhone ? '' : 'cursor-move select-none'"
				@pointerdown="startDrag"
			>
				<div class="flex items-center gap-2">
					<span class="lucide-sparkles size-4 text-ink-gray-7" aria-hidden="true" />
					<span class="text-base font-medium text-ink-gray-9">Assistant</span>
				</div>
				<div class="flex items-center gap-1">
					<Dropdown :options="sessionOptions" placement="right">
						<Button
							variant="ghost"
							icon="lucide-history"
							tooltip="Chat history"
							@click="refreshSessions"
						/>
					</Dropdown>
					<Button
						v-if="sessionId"
						variant="ghost"
						icon="lucide-pencil"
						tooltip="Rename chat"
						@click="openRename"
					/>
					<Button
						v-if="sessionId"
						variant="ghost"
						icon="lucide-archive"
						tooltip="Archive chat"
						@click="chat.archiveSession()"
					/>
					<Button
						variant="ghost"
						icon="lucide-plus"
						tooltip="New chat"
						@click="chat.newSession()"
					/>
					<Button
						v-if="!isPhone"
						variant="ghost"
						icon="lucide-minus"
						tooltip="Minimize"
						@click="minimized = true"
					/>
					<Button
						variant="ghost"
						icon="lucide-x"
						tooltip="Close"
						@click="emit('update:open', false)"
					/>
				</div>
			</header>

			<div ref="listEl" class="flex-1 space-y-3 overflow-y-auto px-3 py-4">
				<div
					v-if="!messages.length"
					class="flex h-full flex-col items-center justify-center gap-2 px-6 text-center text-ink-gray-5"
				>
					<span class="lucide-sparkles size-7" aria-hidden="true" />
					<p class="text-base text-ink-gray-7">Ask about a document's tree</p>
					<p class="text-sm">
						Try: "Summarize the section tree of document &lt;name&gt;".
					</p>
				</div>

				<template v-for="m in messages" :key="m.id">
					<!-- User bubble -->
					<div v-if="m.role === 'user'" class="flex justify-end">
						<div
							class="max-w-[85%] whitespace-pre-wrap rounded-lg bg-surface-gray-3 px-3 py-2 text-base text-ink-gray-9"
						>
							{{ m.content }}
						</div>
					</div>

					<!-- Tool-call card -->
					<div
						v-else-if="m.role === 'tool'"
						class="rounded-lg border border-outline-gray-1 bg-surface-gray-1 px-3 py-2"
					>
						<div class="flex items-center gap-2 text-sm text-ink-gray-7">
							<Spinner v-if="m.status === 'running'" class="size-3.5" />
							<span
								v-else
								class="lucide-check size-3.5 text-ink-green-6"
								aria-hidden="true"
							/>
							<span class="font-medium text-ink-gray-8">{{ m.toolName }}</span>
							<span class="text-ink-gray-5">{{
								TOOL_STATUS_LABEL[m.status] || m.status
							}}</span>
						</div>
					</div>

					<!-- Confirmation card — expensive/destructive tool held for approval -->
					<div
						v-else-if="m.role === 'confirm'"
						class="rounded-lg border border-outline-amber-1 bg-surface-amber-1 px-3 py-2.5"
					>
						<div class="flex items-center gap-2 text-sm font-medium text-ink-gray-8">
							<span
								class="lucide-shield-alert size-4 text-ink-amber-6"
								aria-hidden="true"
							/>
							Confirm {{ m.toolName }}
						</div>
						<p class="mt-1 text-sm text-ink-gray-7">{{ m.summary }}</p>
						<div v-if="m.status === 'pending'" class="mt-2.5 flex gap-2">
							<Button
								variant="solid"
								theme="red"
								label="Run it"
								@click="chat.approveTool(m)"
							/>
							<Button
								variant="outline"
								label="Cancel"
								@click="chat.dismissConfirm(m)"
							/>
						</div>
						<p v-else class="mt-2 text-xs text-ink-gray-5">
							{{ m.status === "approved" ? "Approved — running." : "Cancelled." }}
						</p>
					</div>

					<!-- Applied-changes card — the turn's mutation batch landed (slice 25) -->
					<div
						v-else-if="m.role === 'applied'"
						class="rounded-lg border border-outline-green-1 bg-surface-green-1 px-3 py-2"
					>
						<div class="flex items-center gap-2 text-sm text-ink-green-6">
							<span class="lucide-check-check size-4" aria-hidden="true" />
							<span class="font-medium"
								>Applied {{ m.count }} change{{ m.count === 1 ? "" : "s" }}</span
							>
						</div>
						<p v-if="m.tools?.length" class="mt-1 text-xs text-ink-gray-6">
							{{ m.tools.join(", ") }}
						</p>
					</div>

					<!-- Clarification card — the agent asked a question -->
					<div
						v-else-if="m.role === 'clarify'"
						class="rounded-lg border border-outline-gray-2 bg-surface-gray-1 px-3 py-2.5"
					>
						<div class="flex items-start gap-2 text-base text-ink-gray-9">
							<span
								class="lucide-help-circle mt-0.5 size-4 shrink-0 text-ink-gray-6"
								aria-hidden="true"
							/>
							<span>{{ m.content }}</span>
						</div>
						<div v-if="m.options?.length" class="mt-2.5 flex flex-wrap gap-1.5">
							<Button
								v-for="opt in m.options"
								:key="opt"
								variant="outline"
								:label="opt"
								@click="chat.selectClarifyOption(opt)"
							/>
						</div>
					</div>

					<!-- Assistant bubble -->
					<div v-else class="flex justify-start">
						<div
							class="max-w-[90%] rounded-lg px-3 py-2 text-base"
							:class="
								m.status === 'error'
									? 'bg-surface-red-1 text-ink-red-6'
									: 'bg-surface-gray-2 text-ink-gray-9'
							"
						>
							<MarkdownPreview v-if="m.content" :content="m.content" />
							<span v-else class="flex items-center gap-2 text-ink-gray-5">
								<Spinner class="size-3.5" /> Thinking…
							</span>
						</div>
					</div>
				</template>
			</div>

			<div class="border-t border-outline-gray-1 p-3">
				<!-- Context chips (the removable attachments the agent knows about). -->
				<div v-if="attachments.length" class="mb-2 flex flex-wrap gap-1.5">
					<span
						v-for="att in attachments"
						:key="`${att.type}-${att.name}`"
						class="flex max-w-[14rem] items-center gap-1 rounded-md border border-outline-gray-2 bg-surface-gray-2 py-0.5 pl-1.5 pr-1 text-xs text-ink-gray-7"
					>
						<span
							:class="[CHIP_ICON[att.type], 'size-3 shrink-0']"
							aria-hidden="true"
						/>
						<span class="truncate">{{ att.label || att.name }}</span>
						<button
							class="flex shrink-0 rounded p-0.5 hover:bg-surface-gray-3"
							:aria-label="`Remove ${att.label || att.name}`"
							@click="chat.removeAttachment(att)"
						>
							<span class="lucide-x size-3" aria-hidden="true" />
						</button>
					</span>
				</div>
				<div v-if="errorText" class="mb-2 flex items-center justify-between gap-2">
					<p class="text-sm text-ink-red-6">{{ errorText }}</p>
					<Button
						v-if="showRetry"
						variant="subtle"
						theme="gray"
						size="sm"
						icon-left="lucide-rotate-ccw"
						label="Retry"
						@click="chat.retry()"
					/>
				</div>
				<div class="flex items-end gap-2">
					<Textarea
						v-model="prompt"
						:rows="1"
						placeholder="Ask the assistant…"
						class="max-h-32 flex-1 resize-none"
						@keydown="onKeydown"
					/>
					<Button
						v-if="isRunning"
						variant="ghost"
						icon="lucide-square"
						tooltip="Stop"
						@click="chat.cancel()"
					/>
					<Button
						v-else
						variant="solid"
						icon="lucide-arrow-up"
						:disabled="!prompt.trim()"
						@click="onSubmit"
					/>
				</div>

				<!-- Model picker — the agent model for this session. -->
				<div class="mt-1.5 flex items-center justify-end">
					<Dropdown :options="modelOptions" placement="left">
						<button
							class="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-7"
						>
							<span class="lucide-cpu size-3" aria-hidden="true" />
							<span class="max-w-[12rem] truncate">{{ shortModel(model) }}</span>
							<span class="lucide-chevron-down size-3" aria-hidden="true" />
						</button>
					</Dropdown>
				</div>
			</div>
		</aside>
	</Transition>
</template>
