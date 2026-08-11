<script setup>
import { onMounted, onUnmounted } from "vue";
import { useRouter } from "vue-router";
import { Badge, Button, Progress, Skeleton, useList } from "frappe-ui";
import { useSocket } from "@/socket";
import { statusTheme, isActive } from "@/utils/status";

// Project-scoped imports list, embedded in ProjectDetail. The header + New Import
// button live in the parent; this owns the list body, its empty state, and realtime.
const props = defineProps({
	project: { type: String, required: true },
});
const emit = defineEmits(["new-import"]);

const router = useRouter();

const imports = useList({
	doctype: "Wikify Import",
	fields: ["name", "import_title", "status", "stage_progress", "page_count", "modified"],
	filters: { project: props.project },
	orderBy: "modified desc",
	limit: 50,
});

const socket = useSocket();
function onProgress(payload) {
	const row = imports.data?.find((r) => r.name === payload.import);
	if (row) {
		row.stage_progress = payload.percent;
		if (payload.status) row.status = payload.status;
	} else {
		// New import not in the page yet (just created) — pull it in.
		imports.reload();
	}
}

onMounted(() => socket?.on("wikify_import_progress", onProgress));
onUnmounted(() => socket?.off("wikify_import_progress", onProgress));

function openImport(name) {
	router.push({ name: "ImportDetail", params: { name } });
}

function fmtDate(d) {
	if (!d) return "";
	return new Date(d.replace(" ", "T")).toLocaleString();
}

// A bare number reads as noise once the row stacks and loses its column header.
function pageLabel(row) {
	return row.page_count ? `${row.page_count} pages` : "—";
}
</script>

<template>
	<div class="body-container pt-5 pb-40">
		<!-- Loading skeleton (first load only — reloads keep the rows) -->
		<div
			v-if="imports.loading && !imports.data"
			class="rounded-md border border-outline-gray-1"
		>
			<div
				v-for="i in 5"
				:key="i"
				class="flex items-center gap-4 border-b border-outline-gray-1 px-4 py-3 last:border-b-0"
			>
				<Skeleton class="h-4 flex-1 rounded" />
				<Skeleton class="h-4 w-40 shrink-0 rounded" />
				<Skeleton class="h-4 w-16 shrink-0 rounded" />
				<Skeleton class="h-4 w-44 shrink-0 rounded" />
			</div>
		</div>

		<!-- Empty state -->
		<div
			v-else-if="!imports.loading && (imports.data?.length ?? 0) === 0"
			class="flex flex-col items-center justify-center gap-3 py-16 text-center"
		>
			<div class="rounded-full bg-surface-gray-2 p-3 text-ink-gray-5">
				<span class="lucide-inbox size-6" aria-hidden="true" />
			</div>
			<p class="text-base text-ink-gray-7">No documents yet</p>
			<p class="text-sm text-ink-gray-5">Upload a PDF to add your first document.</p>
			<Button
				variant="solid"
				theme="gray"
				icon-left="lucide-plus"
				label="New Document"
				class="mt-2"
				@click="emit('new-import')"
			/>
		</div>

		<!-- List -->
		<!-- Four fixed columns leave a phone ~80px for the title, so below sm the row
		     wraps into stacked lines and the column header (which labels nothing once
		     they're stacked) drops out. -->
		<div v-else class="rounded-md border border-outline-gray-1">
			<div
				class="hidden items-center gap-4 border-b border-outline-gray-1 px-4 py-2 text-sm text-ink-gray-5 sm:flex"
			>
				<span class="flex-1">Title</span>
				<span class="w-40 shrink-0">Status</span>
				<span class="w-16 shrink-0 text-right">Pages</span>
				<span class="w-44 shrink-0 text-right">Updated</span>
			</div>
			<button
				v-for="row in imports.data"
				:key="row.name"
				class="flex w-full flex-wrap items-center gap-x-4 gap-y-1.5 border-b border-outline-gray-1 px-4 py-2.5 text-left last:border-b-0 hover:bg-surface-gray-2 sm:flex-nowrap"
				@click="openImport(row.name)"
			>
				<span class="w-full truncate text-base text-ink-gray-8 sm:w-auto sm:flex-1">{{
					row.import_title
				}}</span>
				<span class="flex shrink-0 items-center gap-2 sm:w-40">
					<Badge :label="row.status" :theme="statusTheme(row.status)" variant="subtle" />
					<Progress
						v-if="isActive(row.status)"
						:value="row.stage_progress || 0"
						size="sm"
						class="w-16"
					/>
				</span>
				<span class="shrink-0 text-sm text-ink-gray-6 sm:w-16 sm:text-right">
					<span class="sm:hidden">{{ pageLabel(row) }}</span>
					<span class="hidden sm:inline">{{ row.page_count || "—" }}</span>
				</span>
				<span
					class="ml-auto shrink-0 truncate text-sm text-ink-gray-5 sm:ml-0 sm:w-44 sm:text-right"
					>{{ fmtDate(row.modified) }}</span
				>
			</button>
		</div>
	</div>
</template>
