<script setup>
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { Badge, FormControl, PageHeader, useCall, useList } from "frappe-ui";

const router = useRouter();

// Project filter — default "All projects" (empty value spans every document).
const projects = useList({
	doctype: "Wikify Project",
	fields: ["name", "project_name", "is_default"],
	orderBy: "is_default desc, project_name asc",
	limit: 100,
});
const projectOptions = computed(() => [
	{ label: "All projects", value: "" },
	...(projects.data || []).map((p) => ({ label: p.project_name, value: p.name })),
]);
const selectedProject = ref("");

// Fetches driven explicitly (reactive `auto` on useCall is unreliable — Slice 4 gotcha).
const summary = useCall({
	url: "/api/v2/method/wikify.api.explore.type_summary",
	method: "GET",
	immediate: false,
});
const groups = useCall({
	url: "/api/v2/method/wikify.api.explore.sections_by_type",
	method: "GET",
	immediate: false,
});

const types = computed(() => (summary.data || []).filter((t) => t.count > 0));
const matchCount = computed(() => (groups.data || []).reduce((n, g) => n + g.sections.length, 0));

const selectedType = ref(null);
const selected = computed(
	() => types.value.find((t) => t.type_name === selectedType.value) || null
);

// Re-pull the type summary whenever the project scope changes; the selected-type watcher
// then re-pulls the grouped results for the (possibly new) scope.
watch(selectedProject, (project) => summary.submit(project ? { project } : {}), {
	immediate: true,
});
watch(types, (list) => {
	if (list.length && !list.some((t) => t.type_name === selectedType.value)) {
		selectedType.value = list[0].type_name;
	}
});
watch([selectedType, selectedProject], ([t, project]) => {
	if (t) groups.submit(project ? { section_type: t, project } : { section_type: t });
});

function openImport(name) {
	if (name) router.push({ name: "ImportDetail", params: { name, tab: "tree" } });
}
function pageRange(s) {
	if (s.page_start == null) return "";
	return s.page_start === s.page_end ? `p${s.page_start}` : `p${s.page_start}–${s.page_end}`;
}
</script>

<template>
	<div class="flex h-full flex-col">
		<PageHeader>
			<div class="flex min-w-0 items-center gap-3">
				<h1 class="text-md text-ink-gray-9">Explore</h1>
				<span class="truncate text-sm text-ink-gray-5"
					>Sections by type across documents</span
				>
			</div>
			<FormControl
				v-model="selectedProject"
				type="select"
				:options="projectOptions"
				class="w-48"
			/>
		</PageHeader>

		<!-- Empty state: nothing classified anywhere yet -->
		<div
			v-if="!summary.loading && !types.length"
			class="flex flex-1 flex-col items-center justify-center gap-3 text-center"
		>
			<div class="rounded-full bg-surface-gray-2 p-3 text-ink-gray-5">
				<span class="lucide-shapes size-6" aria-hidden="true" />
			</div>
			<p class="text-base text-ink-gray-7">No classified sections yet</p>
			<p class="text-sm text-ink-gray-5">
				Add and parse a PDF — its sections are typed automatically.
			</p>
		</div>

		<div v-else class="flex min-h-0 flex-1">
			<!-- Type rail -->
			<aside class="w-60 shrink-0 overflow-auto border-r border-outline-gray-1 p-2">
				<p class="px-2 py-1.5 text-xs font-medium tracking-wide text-ink-gray-5 uppercase">
					Types
				</p>
				<button
					v-for="t in types"
					:key="t.type_name"
					class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-base"
					:class="
						t.type_name === selectedType
							? 'bg-surface-gray-3 text-ink-gray-9'
							: 'text-ink-gray-7 hover:bg-surface-gray-2'
					"
					@click="selectedType = t.type_name"
				>
					<span
						class="size-2.5 shrink-0 rounded-full"
						:style="{ backgroundColor: t.color }"
						aria-hidden="true"
					/>
					<span class="flex-1 truncate">{{ t.label }}</span>
					<span class="shrink-0 text-sm text-ink-gray-5">{{ t.count }}</span>
				</button>
			</aside>

			<!-- Grouped results -->
			<section class="min-w-0 flex-1 overflow-auto">
				<div
					v-if="selected"
					class="sticky top-0 flex items-center gap-2 border-b border-outline-gray-1 bg-surface-base px-5 py-3"
				>
					<span
						class="size-3 shrink-0 rounded-full"
						:style="{ backgroundColor: selected.color }"
						aria-hidden="true"
					/>
					<h2 class="text-base font-medium text-ink-gray-9">{{ selected.label }}</h2>
					<Badge :label="`${matchCount}`" theme="gray" variant="subtle" size="sm" />
				</div>

				<div class="px-5 py-3">
					<div v-for="g in groups.data || []" :key="g.source_document" class="mb-6">
						<button
							class="mb-1.5 flex items-center gap-2 text-sm font-medium text-ink-gray-8 hover:text-ink-gray-9"
							@click="openImport(g.import_name)"
						>
							<span
								class="lucide-file-text size-4 text-ink-gray-5"
								aria-hidden="true"
							/>
							<span class="truncate">{{ g.doc_title }}</span>
							<Badge
								:label="`${g.sections.length}`"
								theme="gray"
								variant="subtle"
								size="sm"
							/>
						</button>
						<div class="rounded-md border border-outline-gray-1">
							<div
								v-for="s in g.sections"
								:key="s.name"
								class="flex items-center gap-3 border-b border-outline-gray-1 px-4 py-2 last:border-b-0"
							>
								<div class="min-w-0 flex-1">
									<p class="truncate text-base text-ink-gray-8">{{ s.title }}</p>
									<p class="truncate text-xs text-ink-gray-5">
										{{ s.hierarchy_path }}
									</p>
								</div>
								<Badge
									v-if="pageRange(s)"
									:label="pageRange(s)"
									theme="gray"
									variant="subtle"
									size="sm"
								/>
							</div>
						</div>
					</div>
					<p
						v-if="!matchCount && !groups.loading"
						class="py-10 text-center text-sm text-ink-gray-5"
					>
						No sections of this type.
					</p>
				</div>
			</section>
		</div>
	</div>
</template>
