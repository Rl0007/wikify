<script setup>
import { ref, watch } from "vue";
import { Badge, Button, PageHeader, useDoc } from "frappe-ui";
import ImportList from "@/pages/ImportList.vue";
import NewImportDialog from "@/components/NewImportDialog.vue";
import { setProject } from "@/data/agentContext";

const props = defineProps({
	name: { type: String, required: true },
});

const project = useDoc({ doctype: "Wikify Project", name: props.name });

// Attach this project as the agent's default context.
watch(
	() => props.name,
	(name) => name && setProject({ name, label: project.doc?.project_name || name }),
	{ immediate: true }
);
watch(
	() => project.doc?.project_name,
	(label) => label && setProject({ name: props.name, label })
);

const showNewImport = ref(false);
</script>

<template>
	<div>
		<PageHeader>
			<div class="flex min-w-0 items-center gap-2">
				<Button variant="ghost" icon="lucide-arrow-left" :route="{ name: 'Projects' }" />
				<nav class="flex min-w-0 items-center gap-1.5 text-base">
					<RouterLink
						:to="{ name: 'Projects' }"
						class="text-ink-gray-5 hover:text-ink-gray-7"
						>Projects</RouterLink
					>
					<span class="text-ink-gray-4" aria-hidden="true">/</span>
					<span class="truncate text-ink-gray-9">{{
						project.doc?.project_name || name
					}}</span>
				</nav>
				<Badge
					v-if="project.doc?.is_default"
					label="Default"
					theme="gray"
					variant="subtle"
					size="sm"
				/>
			</div>

			<div class="flex items-center gap-2">
				<Button
					variant="subtle"
					label="Graph"
					icon-left="lucide-waypoints"
					:route="{ name: 'ProjectGraph', params: { name } }"
				/>
				<Button
					variant="ghost"
					icon="lucide-settings"
					:route="{ name: 'ProjectSettings', params: { name } }"
				/>
				<Button
					variant="solid"
					theme="gray"
					icon-left="lucide-plus"
					label="New Document"
					@click="showNewImport = true"
				/>
			</div>
		</PageHeader>

		<ImportList :project="name" @new-import="showNewImport = true" />

		<NewImportDialog v-model:open="showNewImport" :project="name" />
	</div>
</template>
