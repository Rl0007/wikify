<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { Badge, Button, Dropdown, Tree, dialog, useCall, useList, toast } from "frappe-ui";
import { Splitpanes, Pane } from "splitpanes";
import "splitpanes/dist/splitpanes.css";
import WikiPreview from "@/components/WikiPreview.vue";
import { useIsNarrow } from "@/composables/useMediaQuery";
import { setSection } from "@/data/agentContext";

const props = defineProps({
	sourceDocument: { type: String, default: null },
	docTitle: { type: String, default: "Document" },
	importName: { type: String, default: null },
	status: { type: String, default: null },
	// Deep-link target (0.5 graph view click-through): selected + scrolled to on load.
	initialSection: { type: String, default: null },
});
const emit = defineEmits(["graphed"]);

// Flat sections ordered by tree position (`lft`); the nesting is rebuilt client-side.
const sections = useList({
	doctype: "Source Section",
	fields: [
		"name",
		"parent_source_section",
		"title",
		"is_group",
		"level",
		"section_type",
		"hierarchy_path",
		"page_start",
		"page_end",
		"sort_order",
		"include_in_wiki",
		"markdown",
		"lint_issues",
	],
	filters: computed(() => ({ source_document: props.sourceDocument || "__none__" })),
	orderBy: "lft asc",
	limit: 1000,
	auto: true,
});
defineExpose({ reload: () => sections.reload() });

const sectionCount = computed(() => (sections.data || []).length);

// Stored `lint_issues` JSON (0.6) → the row badge count; tolerant of the field
// arriving as a string or already-parsed list.
function lintCount(value) {
	if (Array.isArray(value)) return value.length;
	try {
		return value ? JSON.parse(value).length : 0;
	} catch {
		return 0;
	}
}

// Nested tree for frappe-ui Tree. Rebuilt from the flat rows on every server read;
// each node's `expanded` flag is the Tree's per-node state, so carry it across rebuilds.
const tree = ref([]);
const byName = computed(() => {
	const out = {};
	for (const r of sections.data || []) out[r.name] = r;
	return out;
});

watch(
	() => sections.data,
	(rows) => {
		const prevExpanded = {};
		const collect = (list) => {
			for (const n of list) {
				prevExpanded[n.name] = n.expanded;
				collect(n.children);
			}
		};
		collect(tree.value);

		const nodes = {};
		for (const r of rows || [])
			nodes[r.name] = {
				...r,
				lint_count: lintCount(r.lint_issues),
				children: [],
				expanded: prevExpanded[r.name] ?? true,
			};
		const roots = [];
		for (const r of rows || []) {
			const parent = r.parent_source_section;
			if (parent && nodes[parent]) nodes[parent].children.push(nodes[r.name]);
			else roots.push(nodes[r.name]);
		}
		tree.value = roots;
		if (roots.length && !byName.value[selectedName.value]) {
			const wanted =
				props.initialSection && nodes[props.initialSection] ? props.initialSection : null;
			selectedName.value = wanted || roots[0].name;
			if (wanted)
				nextTick(() =>
					document
						.querySelector(`[data-section-row="${wanted}"]`)
						?.scrollIntoView({ block: "center" })
				);
		}
	},
	{ immediate: true, deep: false }
);

const selectedName = ref(null);
const selected = computed(() => byName.value[selectedName.value] || null);

// Narrow screens drill down instead of splitting: the tree fills the width, tapping a
// section swaps in its preview, Back returns. Swapping the Splitpanes host for a plain
// <div> keeps one copy of the markup rather than forking a phone-only template.
const isNarrow = useIsNarrow();
const showPreview = ref(!!props.initialSection);
const SplitHost = computed(() => (isNarrow.value ? "div" : Splitpanes));
const SplitPane = computed(() => (isNarrow.value ? "div" : Pane));
function onSelect(name) {
	selectedName.value = name;
	showPreview.value = true;
}

// Attach the selected section as the agent's default context (swaps out any page chip).
watch(selected, (s) => {
	if (s) setSection({ name: s.name, label: s.title || "Section" });
});

// --- Mutations -----------------------------------------------------------------------
// useCall.submit resolves (doesn't reject) on a server error and sets `.error`, so we
// inspect that. Every mutation re-reads the list from the server to pick up the
// re-derived lft/level/hierarchy_path/is_group (and to revert an optimistic drag on
// failure).
const mutating = ref(false);
async function mutate(call, params) {
	mutating.value = true;
	try {
		await call.submit(params);
		if (call.error) throw call.error;
	} catch (e) {
		toast.error(e?.messages?.[0] || e?.message || "Could not save change");
	} finally {
		await sections.reload();
		mutating.value = false;
	}
}

function sectionCall(method) {
	return useCall({
		url: `/api/v2/method/wikify.api.sections.${method}`,
		method: "POST",
		immediate: false,
	});
}
const reorder = sectionCall("reorder_section");
const rename = sectionCall("rename_section");
const toggle = sectionCall("toggle_include");
const remove = sectionCall("delete_section");
const graph = sectionCall("build_graph");

// --- Drag and drop ---------------------------------------------------------------------
// Reordering among siblings is always fine; reparenting only into groups (same rule the
// old handle-drag enforced by never rendering a drop zone inside leaves).
function canMove({ target, position }) {
	return position !== "inside" || !!target.is_group;
}

function findNode(name, list = tree.value) {
	for (const n of list) {
		if (n.name === name) return n;
		const hit = findNode(name, n.children);
		if (hit) return hit;
	}
	return null;
}

// The Tree hands us the committed move; report the node's new parent + the post-drop
// sibling order so the server can rewrite sort_order and rebuild the tree.
function onDragEnd(info) {
	if (!info) return;
	const { node, to, newIndex } = info;
	const target = to ? findNode(to)?.children || [] : tree.value;
	const siblings = target.map((n) => n.name).filter((n) => n !== node.name);
	siblings.splice(newIndex, 0, node.name);
	mutate(reorder, { name: node.name, new_parent: to, new_index: newIndex, siblings });
}

// --- Inline rename -----------------------------------------------------------------------
const editingKey = ref(null);
const draft = ref("");

// Focus + select the rename input as soon as it mounts.
const vFocus = { mounted: (el) => el.focus() };

function startRename(node) {
	editingKey.value = node.name;
	draft.value = node.title;
}
function commitRename(node) {
	const title = draft.value.trim();
	editingKey.value = null;
	if (title && title !== node.title) mutate(rename, { name: node.name, title });
}

function onToggleInclude(node) {
	mutate(toggle, { name: node.name, include: node.include_in_wiki ? 0 : 1 });
}

function pageRange(node) {
	if (node.page_start == null) return null;
	return node.page_start === node.page_end
		? `p${node.page_start}`
		: `p${node.page_start}–${node.page_end}`;
}

function rowActions(node) {
	return [
		{ label: "Rename", icon: "lucide-pencil", onClick: () => startRename(node) },
		{
			label: node.include_in_wiki ? "Exclude from wiki" : "Include in wiki",
			icon: node.include_in_wiki ? "lucide-eye-off" : "lucide-eye",
			onClick: () => onToggleInclude(node),
		},
		{ label: "Delete", icon: "lucide-trash-2", onClick: () => onRemove(node) },
	];
}

// Delete with a cascade-aware confirm.
function subtreeSize(node) {
	return 1 + (node.children || []).reduce((n, c) => n + subtreeSize(c), 0);
}
function onRemove(node) {
	const nested = subtreeSize(node) - 1;
	const suffix = nested ? ` and its ${nested} nested section${nested === 1 ? "" : "s"}` : "";
	dialog.danger({
		title: "Delete section",
		message: `Delete "${node.title}"${suffix}? This can't be undone.`,
		confirmLabel: "Delete",
		onConfirm() {
			if (selectedName.value === node.name) selectedName.value = null;
			mutate(remove, { name: node.name });
		},
	});
}

// --- Approve & build graph -----------------------------------------------------------
const isGraphed = computed(() => props.status === "Graphed");
// "Approve & Build Graph" pushes the toolbar past a phone's width; the shorter label
// says the same thing next to the section count it sits beside.
const graphLabel = computed(() => {
	if (isGraphed.value) return "Rebuild graph";
	return isNarrow.value ? "Build graph" : "Approve & Build Graph";
});
async function buildGraph() {
	try {
		await graph.submit({ import_name: props.importName });
		toast.success("Graph built — Explore & Wiki unlocked");
		emit("graphed");
	} catch (e) {
		toast.error(e?.messages?.[0] || e?.message || "Could not build graph");
	}
}
</script>

<template>
	<div class="h-full">
		<p
			v-if="sections.loading && !sections.data"
			class="py-10 text-center text-sm text-ink-gray-5"
		>
			Loading sections…
		</p>
		<p v-else-if="!sectionCount" class="py-10 text-center text-sm text-ink-gray-5">
			No sections yet — parse the document to build its tree.
		</p>

		<component :is="SplitHost" v-else :class="isNarrow ? 'flex h-full flex-col' : 'h-full'">
			<!-- Left: the editable section tree -->
			<component
				:is="SplitPane"
				v-show="!isNarrow || !showPreview"
				:size="isNarrow ? undefined : 40"
				:min-size="isNarrow ? undefined : 25"
				class="flex flex-col"
				:class="isNarrow ? 'min-h-0 flex-1' : 'border-r border-outline-gray-1'"
			>
				<div
					class="flex flex-wrap items-center gap-2 border-b border-outline-gray-1 px-3 py-2 text-sm text-ink-gray-6"
				>
					<span class="font-medium text-ink-gray-8">Sections</span>
					<Badge :label="String(sectionCount)" theme="gray" variant="subtle" size="sm" />
					<Badge
						v-if="isGraphed"
						label="Graphed"
						theme="green"
						variant="subtle"
						size="sm"
					/>
					<span v-if="mutating" class="text-xs text-ink-gray-4">Saving…</span>
					<Button
						class="ml-auto shrink-0"
						size="sm"
						variant="solid"
						:label="graphLabel"
						:loading="graph.loading"
						@click="buildGraph"
					/>
				</div>
				<!-- A tighter indent on narrow screens: ICAI titles run to 140 chars and every
				     nesting step is width the title no longer gets. -->
				<div class="flex-1 overflow-auto p-2 [--tree-indent:14px] lg:[--tree-indent:24px]">
					<Tree
						:nodes="tree"
						node-key="name"
						draggable
						:move="canMove"
						:disabled="mutating"
						@drag-end="onDragEnd"
					>
						<template #item="{ node, expanded, hasChildren, toggle: toggleNode }">
							<div
								class="group flex min-w-0 flex-1 items-center gap-1.5 rounded py-0.5 pr-1"
								:class="selectedName === node.name ? 'bg-surface-gray-3' : ''"
								:data-section-row="node.name"
							>
								<button
									v-if="hasChildren"
									class="shrink-0 rounded p-0.5 text-ink-gray-5 hover:bg-surface-gray-3"
									:aria-label="expanded ? 'Collapse' : 'Expand'"
									@click.stop="toggleNode"
								>
									<span
										class="lucide-chevron-right size-4 transition-transform"
										:class="expanded ? 'rotate-90' : ''"
										aria-hidden="true"
									/>
								</button>
								<span v-else class="w-5 shrink-0" />

								<!-- Inline rename, else the clickable title -->
								<input
									v-if="editingKey === node.name"
									v-model="draft"
									class="min-w-0 flex-1 rounded border border-outline-gray-2 bg-surface-base px-1 py-0.5 text-sm text-ink-gray-9 outline-none focus:border-outline-gray-4"
									@click.stop
									@dragstart.prevent.stop
									@keydown.enter.prevent="commitRename(node)"
									@keydown.esc.prevent="editingKey = null"
									@blur="commitRename(node)"
									v-focus
								/>
								<button
									v-else
									class="flex min-w-0 flex-1 items-center gap-2 text-left"
									@click.stop="onSelect(node.name)"
								>
									<span
										class="min-w-0 truncate text-sm"
										:class="
											node.include_in_wiki
												? 'text-ink-gray-8'
												: 'text-ink-gray-4 line-through'
										"
										>{{ node.title }}</span
									>
									<!-- The type is one of several chips competing with a 140-char
									     title; on narrow screens the title wins (Explore groups
									     by type anyway). -->
									<Badge
										v-if="node.section_type"
										:label="node.section_type"
										theme="blue"
										variant="subtle"
										size="sm"
										class="hidden lg:inline-flex"
									/>
									<Badge
										v-if="node.lint_count"
										:label="`⚠ ${node.lint_count}`"
										theme="orange"
										variant="subtle"
										size="sm"
										:title="`${node.lint_count} markdown issue${
											node.lint_count === 1 ? '' : 's'
										} — open the preview for details`"
									/>
									<span
										v-if="pageRange(node)"
										class="shrink-0 text-xs tabular-nums text-ink-gray-4"
										>{{ pageRange(node) }}</span
									>
								</button>

								<!-- Touch has no hover, so the row menu stays visible on narrow
								     screens instead of being hover-revealed. -->
								<Dropdown :options="rowActions(node)" placement="right">
									<button
										class="shrink-0 rounded p-0.5 text-ink-gray-5 hover:bg-surface-gray-3 lg:opacity-0 lg:group-hover:opacity-100"
										@click.stop
									>
										<span
											class="lucide-more-horizontal size-4"
											aria-hidden="true"
										/>
									</button>
								</Dropdown>
							</div>
						</template>
					</Tree>
				</div>
			</component>

			<!-- Right: wiki-fidelity preview of the selected section -->
			<component
				:is="SplitPane"
				v-show="!isNarrow || showPreview"
				:size="isNarrow ? undefined : 60"
				class="flex flex-col"
				:class="isNarrow ? 'min-h-0 flex-1' : ''"
			>
				<WikiPreview
					:section="selectedName"
					:show-back="isNarrow"
					@navigate="onSelect"
					@back="showPreview = false"
				/>
			</component>
		</component>
	</div>
</template>
