<script setup>
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
	Dialog,
	FileUploader,
	FormControl,
	Button,
	ErrorMessage,
	useCall,
	useList,
} from "frappe-ui";

const open = defineModel("open", { type: Boolean, default: false });
// When launched from a project, that project is preset; globally it falls back to the
// seeded "Uncategorized" default.
const props = defineProps({
	project: { type: String, default: "" },
});

const router = useRouter();
const pdfUrl = ref(null);
const pdfName = ref("");
const title = ref("");
const project = ref(props.project);

// Project picker options — pinned default first (server orders is_default desc).
const projects = useList({
	doctype: "Wikify Project",
	fields: ["name", "project_name", "is_default"],
	orderBy: "is_default desc, project_name asc",
	limit: 100,
});
const projectOptions = computed(() =>
	(projects.data || []).map((p) => ({ label: p.project_name, value: p.name }))
);

// Default the picker: the prop's project, else the seeded default once options load.
watch(
	[() => open.value, projectOptions],
	([isOpen, opts]) => {
		if (!isOpen || project.value) return;
		project.value = props.project || opts.find((o) => o)?.value || "";
	},
	{ immediate: true }
);

const startImport = useCall({
	url: "/api/v2/method/wikify.api.imports.start_import",
	method: "POST",
	immediate: false,
	onSuccess(name) {
		reset();
		open.value = false;
		router.push({ name: "ImportDetail", params: { name } });
	},
});

function onUpload(file) {
	pdfUrl.value = file.file_url;
	pdfName.value = file.file_name;
	// Default the title to the filename without extension; stays editable.
	if (!title.value) {
		title.value = (file.file_name || "").replace(/\.pdf$/i, "");
	}
}

function start() {
	if (!pdfUrl.value || !title.value) return;
	startImport.submit({
		pdf_file_url: pdfUrl.value,
		title: title.value,
		project: project.value || undefined,
	});
}

function reset() {
	pdfUrl.value = null;
	pdfName.value = "";
	title.value = "";
	project.value = props.project;
	startImport.reset();
}
</script>

<template>
	<Dialog v-model:open="open" title="New Document" @close="reset">
		<template #default>
			<div class="space-y-4">
				<div>
					<span class="mb-1.5 block text-xs text-ink-gray-5">PDF</span>
					<FileUploader
						:file-types="'application/pdf'"
						:upload-args="{ private: true }"
						@success="onUpload"
					>
						<template #default="{ openFileSelector, uploading, progress }">
							<div class="flex min-w-0 items-center gap-3">
								<Button
									:loading="uploading"
									:label="
										uploading
											? `Uploading ${progress}%`
											: pdfUrl
											? 'Replace PDF'
											: 'Choose PDF'
									"
									icon-left="lucide-upload"
									@click="openFileSelector"
								/>
								<span
									v-if="pdfName"
									class="min-w-0 truncate text-sm text-ink-gray-7"
									>{{ pdfName }}</span
								>
							</div>
						</template>
					</FileUploader>
				</div>

				<FormControl
					v-model="project"
					label="Project"
					type="select"
					:options="projectOptions"
				/>

				<FormControl
					v-model="title"
					label="Title"
					type="text"
					placeholder="Document title"
					:disabled="!pdfUrl"
				/>

				<ErrorMessage :message="startImport.error?.message" />
			</div>
		</template>

		<template #actions>
			<Button
				variant="solid"
				theme="gray"
				label="Start"
				:loading="startImport.loading"
				:disabled="!pdfUrl || !title"
				@click="start"
			/>
		</template>
	</Dialog>
</template>
