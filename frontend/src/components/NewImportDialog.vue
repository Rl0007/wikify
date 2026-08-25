<script setup>
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { Dialog, FormControl, Button, ErrorMessage, toast, useList } from "frappe-ui";
import { usePdfUpload } from "@/composables/usePdfUpload";
import UploadQueue from "@/components/UploadQueue.vue";

const open = defineModel("open", { type: Boolean, default: false });
// When launched from a project, that project is preset; globally it falls back to the
// seeded "Uncategorized" default.
const props = defineProps({
	project: { type: String, default: "" },
});

const router = useRouter();
const project = ref(props.project);
const fileInput = ref(null);
const dragging = ref(false);

// One upload path shared with the document list's drop zone.
const {
	rows,
	uploaded,
	busy,
	starting,
	error: startError,
	addFiles,
	removeRow,
	upload,
	start,
	reset: resetQueue,
} = usePdfUpload();

// A single PDF keeps the old flow — editable title, route to the detail page. A batch
// takes filenames as titles and stays put.
const single = computed(() => rows.value.length === 1);
const canStart = computed(
	() => !busy.value && uploaded.value.length > 0 && (!single.value || !!rows.value[0].title)
);

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

/** Queue the picked/dropped files and start uploading right away. */
function take(fileList) {
	if (!addFiles(fileList)) return;
	upload();
}

function onPick(e) {
	take(e.target.files);
	// Allow re-picking the same file after a remove.
	e.target.value = "";
}

function onDrop(e) {
	dragging.value = false;
	take(e.dataTransfer?.files);
}

async function submit() {
	const wasSingle = single.value;
	const names = await start(project.value || undefined);
	if (!names.length) return;
	open.value = false;
	reset();
	if (wasSingle) {
		router.push({ name: "ImportDetail", params: { name: names[0] } });
	} else {
		toast.success(`${names.length} documents queued`);
	}
}

function reset() {
	resetQueue();
	project.value = props.project;
}
</script>

<template>
	<Dialog v-model:open="open" :title="single ? 'New Document' : 'New Documents'" @close="reset">
		<template #default>
			<div class="space-y-4">
				<div>
					<span class="mb-1.5 block text-xs text-ink-gray-5">PDFs</span>
					<input
						ref="fileInput"
						type="file"
						accept="application/pdf"
						multiple
						class="hidden"
						@change="onPick"
					/>
					<div
						class="flex flex-col items-center justify-center gap-2 rounded-md border border-dashed px-4 py-6 text-center"
						:class="
							dragging
								? 'border-outline-gray-3 bg-surface-gray-2'
								: 'border-outline-gray-2'
						"
						@dragenter.prevent="dragging = true"
						@dragover.prevent="dragging = true"
						@dragleave="dragging = false"
						@drop.prevent="onDrop"
					>
						<Button
							label="Choose PDFs"
							icon-left="lucide-upload"
							:disabled="busy"
							@click="fileInput?.click()"
						/>
						<p class="text-xs text-ink-gray-5">
							or drop them here — one document each
						</p>
					</div>
				</div>

				<UploadQueue
					v-if="rows.length"
					:rows="rows"
					:removable="!busy"
					@remove="removeRow"
					@retry="upload()"
				/>

				<FormControl
					v-model="project"
					label="Project"
					type="select"
					:options="projectOptions"
				/>

				<FormControl
					v-if="single"
					v-model="rows[0].title"
					label="Title"
					type="text"
					placeholder="Document title"
				/>

				<ErrorMessage :message="startError" />
			</div>
		</template>

		<template #actions>
			<Button
				variant="solid"
				theme="gray"
				:label="rows.length > 1 ? `Start (${uploaded.length})` : 'Start'"
				:loading="starting"
				:disabled="!canStart"
				@click="submit"
			/>
		</template>
	</Dialog>
</template>
