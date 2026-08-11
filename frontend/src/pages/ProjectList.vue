<script setup>
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import {
	Badge,
	Button,
	Dialog,
	ErrorMessage,
	FormControl,
	PageHeader,
	Skeleton,
	useCall,
	useList,
} from "frappe-ui";
import { useIsMobile } from "@/composables/useMediaQuery";
import { actionButtonProps } from "@/utils/actionButton";
import { clear as clearAgentContext } from "@/data/agentContext";

const router = useRouter();
const isMobile = useIsMobile();

// Global landing — the agent opens with no default attachment here.
onMounted(clearAgentContext);

// The Projects landing screen — cards via useList (default "Uncategorized" pinned first
// through the is_default ordering). import_count is denormalized on the doctype.
const projects = useList({
	doctype: "Wikify Project",
	fields: ["name", "project_name", "description", "status", "is_default", "import_count"],
	orderBy: "is_default desc, project_name asc",
	limit: 100,
});

// Archived projects are hidden by default; toggle reveals them (the seeded "Uncategorized"
// default is never archivable, so it always shows).
const showArchived = ref(false);
const visibleProjects = computed(() =>
	(projects.data || []).filter((p) => showArchived.value || p.status !== "Archived"),
);
const archivedCount = computed(
	() => (projects.data || []).filter((p) => p.status === "Archived").length,
);

const showNew = ref(false);
const newName = ref("");
const newDescription = ref("");

const createProject = useCall({
	url: "/api/v2/method/wikify.api.projects.create_project",
	method: "POST",
	immediate: false,
	onSuccess(name) {
		resetNew();
		showNew.value = false;
		router.push({ name: "ProjectDetail", params: { name } });
	},
});

function create() {
	if (!newName.value.trim()) return;
	createProject.submit({ project_name: newName.value, description: newDescription.value });
}

function resetNew() {
	newName.value = "";
	newDescription.value = "";
	createProject.reset();
}

function openProject(name) {
	router.push({ name: "ProjectDetail", params: { name } });
}
</script>

<template>
	<div>
		<PageHeader>
			<h1 class="text-md text-ink-gray-9">Projects</h1>
			<div class="flex shrink-0 items-center gap-2 pl-2">
				<Button
					v-if="archivedCount"
					variant="ghost"
					theme="gray"
					v-bind="
						actionButtonProps(
							isMobile,
							'lucide-archive',
							showArchived ? 'Hide archived' : `Show archived (${archivedCount})`
						)
					"
					@click="showArchived = !showArchived"
				/>
				<Button
					variant="solid"
					theme="gray"
					v-bind="actionButtonProps(isMobile, 'lucide-plus', 'New Project')"
					@click="showNew = true"
				/>
			</div>
		</PageHeader>

		<div class="body-container pt-5 pb-40">
			<!-- Loading skeleton (first load only — reloads keep the cards) -->
			<div
				v-if="projects.loading && !projects.data"
				class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
			>
				<div
					v-for="i in 6"
					:key="i"
					class="flex flex-col gap-2 rounded-md border border-outline-gray-1 p-4"
				>
					<div class="flex items-center gap-2">
						<Skeleton class="size-4 rounded" />
						<Skeleton class="h-4 w-32 rounded" />
					</div>
					<Skeleton class="h-4 w-44 rounded" />
					<Skeleton class="mt-2 h-3 w-24 rounded" />
				</div>
			</div>

			<!-- Empty state -->
			<div
				v-else-if="!projects.loading && visibleProjects.length === 0"
				class="flex flex-col items-center justify-center gap-3 py-16 text-center"
			>
				<div class="rounded-full bg-surface-gray-2 p-3 text-ink-gray-5">
					<span class="lucide-folder size-6" aria-hidden="true" />
				</div>
				<p class="text-base text-ink-gray-7">No projects yet</p>
				<p class="text-sm text-ink-gray-5">Create a project, then upload PDFs into it.</p>
				<Button
					variant="solid"
					theme="gray"
					icon-left="lucide-plus"
					label="New Project"
					class="mt-2"
					@click="showNew = true"
				/>
			</div>

			<!-- Cards -->
			<div v-else class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
				<button
					v-for="p in visibleProjects"
					:key="p.name"
					class="flex flex-col gap-2 rounded-md border border-outline-gray-1 p-4 text-left hover:bg-surface-gray-2"
					@click="openProject(p.name)"
				>
					<div class="flex items-center gap-2">
						<span
							class="lucide-folder size-4 shrink-0 text-ink-gray-5"
							aria-hidden="true"
						/>
						<span class="min-w-0 flex-1 truncate text-base text-ink-gray-8">{{
							p.project_name
						}}</span>
						<Badge
							v-if="p.is_default"
							label="Default"
							theme="gray"
							variant="subtle"
							size="sm"
						/>
						<Badge
							v-else-if="p.status === 'Archived'"
							label="Archived"
							theme="orange"
							variant="subtle"
							size="sm"
						/>
					</div>
					<p class="line-clamp-2 min-h-[2.5rem] text-sm text-ink-gray-5">
						{{ p.description || "No description" }}
					</p>
					<p class="text-sm text-ink-gray-6">
						{{ p.import_count || 0 }} document{{ p.import_count === 1 ? "" : "s" }}
					</p>
				</button>
			</div>
		</div>

		<Dialog v-model:open="showNew" title="New Project" @close="resetNew">
			<template #default>
				<div class="space-y-4">
					<FormControl
						v-model="newName"
						label="Project name"
						type="text"
						placeholder="e.g. Nephrology Manuals"
					/>
					<FormControl
						v-model="newDescription"
						label="Description"
						type="textarea"
						placeholder="Short blurb shown on the project card (optional)"
					/>
					<ErrorMessage :message="createProject.error?.message" />
				</div>
			</template>
			<template #actions>
				<Button
					variant="solid"
					theme="gray"
					label="Create"
					:loading="createProject.loading"
					:disabled="!newName.trim()"
					@click="create"
				/>
			</template>
		</Dialog>
	</div>
</template>
