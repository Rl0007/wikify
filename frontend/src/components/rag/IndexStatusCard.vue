<script setup>
// What the retriever actually has in it — counts, embedding dim, freshness — plus the
// one action that fixes a stale index.
import { computed } from "vue";
import { Button, dayjs } from "frappe-ui";

const props = defineProps({
	status: { type: Object, default: null },
	loading: { type: Boolean, default: false },
	errorText: { type: String, default: "" },
	reindexing: { type: Boolean, default: false },
	jobName: { type: String, default: "" },
	canReindex: { type: Boolean, default: false },
});

defineEmits(["reindex"]);

const metrics = computed(() => [
	{ label: "Chunks", value: props.status?.chunks },
	{ label: "Sections", value: props.status?.sections },
	{ label: "Documents", value: props.status?.documents },
	{ label: "Embedding dim", value: props.status?.dim },
]);

const indexedAt = computed(() =>
	props.status?.indexed_at ? dayjs(props.status.indexed_at).format("D MMM YYYY, h:mm a") : "",
);
</script>

<template>
	<section class="rounded-lg border border-outline-gray-2 bg-surface-elevation-1">
		<header class="flex flex-wrap items-center gap-2 border-b border-outline-gray-2 px-4 py-3">
			<span class="lucide-database size-4 text-ink-gray-5" aria-hidden="true" />
			<h2 class="text-base font-medium text-ink-gray-9">Index status</h2>
			<span
				v-if="status?.stale"
				class="rounded-full bg-surface-amber-2 px-2 py-0.5 text-xs font-medium text-ink-gray-9"
			>
				stale
			</span>
			<span
				v-else-if="status"
				class="rounded-full bg-surface-green-2 px-2 py-0.5 text-xs font-medium text-ink-gray-9"
			>
				fresh
			</span>
			<div class="flex-1" />
			<!-- Reindexing is per-project, so with "All projects" selected there is nothing
			     to rebuild; say that instead of showing a dead button. -->
			<span v-if="!canReindex" class="text-xs text-ink-gray-5">
				Pick a project to reindex
			</span>
			<Button
				v-else
				variant="subtle"
				icon-left="lucide-refresh-cw"
				label="Reindex"
				:loading="reindexing"
				tooltip="Rebuild this project’s index"
				@click="$emit('reindex')"
			/>
		</header>

		<div class="px-4 py-4">
			<p v-if="errorText" class="text-sm text-ink-red-8">{{ errorText }}</p>

			<!-- The skeleton mirrors the tile grid, so the card keeps its height while the
			     index is read instead of collapsing to a line of text. -->
			<div v-else-if="loading && !status" class="grid grid-cols-2 gap-3 sm:grid-cols-4">
				<div
					v-for="metric in metrics"
					:key="metric.label"
					class="rounded-md bg-surface-gray-1 px-3 py-2"
				>
					<div class="h-3 w-16 animate-pulse rounded bg-surface-gray-3" />
					<div class="mt-1.5 h-5 w-10 animate-pulse rounded bg-surface-gray-3" />
				</div>
			</div>

			<p v-else-if="!status" class="text-sm text-ink-gray-5">
				No index yet — pick a project and reindex to build one.
			</p>

			<dl v-else class="grid grid-cols-2 gap-3 sm:grid-cols-4">
				<div
					v-for="metric in metrics"
					:key="metric.label"
					class="rounded-md bg-surface-gray-1 px-3 py-2"
				>
					<!-- Fixed label height keeps the numbers on one baseline when a label
					     wraps to two lines ("Embedding dim" at narrow widths). -->
					<dt class="flex h-8 items-start text-xs text-ink-gray-5">
						{{ metric.label }}
					</dt>
					<dd class="text-lg font-medium tabular-nums text-ink-gray-9">
						{{ metric.value ?? "—" }}
					</dd>
				</div>
			</dl>

			<p v-if="indexedAt" class="mt-3 text-xs text-ink-gray-5">
				Last indexed {{ indexedAt }}
			</p>
			<p v-if="jobName" class="mt-1 text-xs text-ink-gray-5">
				Reindex queued as <span class="font-mono break-all">{{ jobName }}</span>
			</p>
		</div>
	</section>
</template>
