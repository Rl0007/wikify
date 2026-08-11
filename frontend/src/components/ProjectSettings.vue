<script setup>
import { computed, ref, watch } from "vue";
import { Button, ErrorMessage, FormControl, PageHeader, toast, useCall, useDoc } from "frappe-ui";

const props = defineProps({
	name: { type: String, required: true },
});

const project = useDoc({ doctype: "Wikify Project", name: props.name });

// Model override picker — blank means "use the site default" (Wikify Settings → built-in).
const agentModels = useCall({
	url: "/api/v2/method/wikify.api.agent.get_agent_models",
	method: "GET",
	immediate: true,
});
const modelOptions = computed(() => [
	{ label: "Site default", value: "" },
	...(agentModels.data || []).map((m) => ({ label: m, value: m })),
]);

// Local editable copy, seeded once the doc arrives (and re-seeded after a save reload).
const form = ref({
	project_name: "",
	description: "",
	context_prompt: "",
	agent_model: "",
	archived: false,
});

watch(
	() => project.doc,
	(doc) => {
		if (!doc) return;
		form.value = {
			project_name: doc.project_name || "",
			description: doc.description || "",
			context_prompt: doc.context_prompt || "",
			agent_model: doc.agent_model || "",
			archived: doc.status === "Archived",
		};
	},
	{ immediate: true }
);

const save = useCall({
	url: "/api/v2/method/wikify.api.projects.update_project",
	method: "POST",
	immediate: false,
	onSuccess() {
		project.reload();
		toast.success("Project settings saved");
	},
});

function submit() {
	if (!form.value.project_name.trim()) return;
	save.submit({
		name: props.name,
		project_name: form.value.project_name,
		description: form.value.description,
		context_prompt: form.value.context_prompt,
		agent_model: form.value.agent_model,
		status: form.value.archived ? "Archived" : "Active",
	});
}
</script>

<template>
	<div>
		<PageHeader>
			<div class="flex min-w-0 items-center gap-2">
				<Button
					variant="ghost"
					icon="lucide-arrow-left"
					:route="{ name: 'ProjectDetail', params: { name } }"
				/>
				<nav class="flex min-w-0 items-center gap-1.5 text-base">
					<RouterLink
						:to="{ name: 'Projects' }"
						class="text-ink-gray-5 hover:text-ink-gray-7"
						>Projects</RouterLink
					>
					<span class="text-ink-gray-4" aria-hidden="true">/</span>
					<RouterLink
						:to="{ name: 'ProjectDetail', params: { name } }"
						class="truncate text-ink-gray-5 hover:text-ink-gray-7"
						>{{ project.doc?.project_name || name }}</RouterLink
					>
					<span class="text-ink-gray-4" aria-hidden="true">/</span>
					<span class="truncate text-ink-gray-9">Settings</span>
				</nav>
			</div>
		</PageHeader>

		<div class="body-container pt-5 pb-40">
			<div class="flex max-w-2xl flex-col gap-5">
				<FormControl
					v-model="form.project_name"
					label="Project name"
					type="text"
					placeholder="e.g. Nephrology Manuals"
				/>

				<FormControl
					v-model="form.description"
					label="Description"
					type="textarea"
					placeholder="Short blurb shown on the project card (optional)"
				/>

				<div>
					<FormControl
						v-model="form.context_prompt"
						label="Context prompt"
						type="textarea"
						:rows="8"
						placeholder="e.g. This is a surgical manual. Prefer British spelling. Always say 'anaesthesia', never 'anesthesia'."
					/>
					<p class="mt-1.5 text-p-sm text-ink-gray-5">
						Passed to every AI step (cleanup, re-parse, classification) and the
						assistant. Steer terminology, audience, and house style. Blank is fine.
					</p>
				</div>

				<div>
					<FormControl
						v-model="form.agent_model"
						label="Agent model"
						type="select"
						:options="modelOptions"
					/>
					<p class="mt-1.5 text-p-sm text-ink-gray-5">
						Optional per-project override for the assistant model. "Site default" uses
						the model set in Wikify Settings.
					</p>
				</div>

				<FormControl
					v-model="form.archived"
					label="Archive this project"
					type="checkbox"
				/>

				<ErrorMessage :message="save.error?.message" />

				<div>
					<Button
						variant="solid"
						theme="gray"
						label="Save"
						:loading="save.loading"
						:disabled="!form.project_name.trim()"
						@click="submit"
					/>
				</div>
			</div>
		</div>
	</div>
</template>
