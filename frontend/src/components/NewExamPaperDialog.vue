<script setup>
// Upload a past question paper. Session metadata is asked for rather than inferred: the
// exam month and assessment year decide which heatmap column the paper lands in and which
// law it was set under, and a PDF filename like "89279bos-aps3012-final-p4.pdf" tells you
// neither. Guessing wrong files a paper under the wrong year silently.
import { computed, ref } from "vue";
import { Button, Dialog, FileUploader, FormControl, ErrorMessage } from "frappe-ui";
import { useUploadPaper } from "@/composables/useExam";

const props = defineProps({ project: { type: String, default: null } });
const emit = defineEmits(["uploaded"]);
const open = defineModel("open", { type: Boolean, default: false });

const MONTHS = ["January", "May", "July", "September", "November", "December"];

const pdfUrl = ref("");
const pdfName = ref("");
const examMonth = ref("May");
const examYear = ref(new Date().getFullYear());
const assessmentYear = ref("");
const paperCode = ref("Paper 4");

const { upload, uploading, errorText } = useUploadPaper();

// ICAI renamed Direct Tax from Paper 7 to Paper 4 with the 2023 scheme, effective May 2024.
// Offered as a default rather than forced, because a user may be uploading another subject.
const suggestedCode = computed(() => (examYear.value >= 2024 ? "Paper 4" : "Paper 7"));

function onUpload(file) {
	pdfUrl.value = file.file_url;
	pdfName.value = file.file_name;
}

function reset() {
	pdfUrl.value = "";
	pdfName.value = "";
	assessmentYear.value = "";
}

const canSubmit = computed(
	() => pdfUrl.value && props.project && examMonth.value && examYear.value && !uploading.value,
);

async function submit() {
	const created = await upload({
		project: props.project,
		file_url: pdfUrl.value,
		exam_month: examMonth.value,
		exam_year: examYear.value,
		assessment_year: assessmentYear.value || null,
		paper_code: paperCode.value || null,
	});
	if (created) {
		emit("uploaded", created);
		open.value = false;
		reset();
	}
}
</script>

<template>
	<Dialog v-model="open" :options="{ title: 'Add question paper' }" @close="reset">
		<template #body-content>
			<div class="space-y-4">
				<div>
					<span class="mb-1.5 block text-xs text-ink-gray-5">Question paper PDF</span>
					<FileUploader
						:file-types="'application/pdf'"
						:upload-args="{ private: true }"
						@success="onUpload"
					>
						<template #default="{ openFileSelector, uploading: busy, progress }">
							<div class="flex min-w-0 items-center gap-3">
								<Button
									:loading="busy"
									:label="
										busy
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
								>
									{{ pdfName }}
								</span>
							</div>
						</template>
					</FileUploader>
				</div>

				<div class="grid grid-cols-2 gap-3">
					<FormControl
						v-model="examMonth"
						label="Exam month"
						type="select"
						:options="MONTHS"
					/>
					<FormControl v-model.number="examYear" label="Exam year" type="number" />
				</div>

				<div class="grid grid-cols-2 gap-3">
					<FormControl
						v-model="assessmentYear"
						label="Assessment year"
						type="text"
						placeholder="2024-25"
						description="Printed on the paper. Decides which law applied."
					/>
					<FormControl
						v-model="paperCode"
						label="Paper"
						type="text"
						:placeholder="suggestedCode"
					/>
				</div>

				<p class="text-xs text-ink-gray-5">
					Extraction runs in the background and takes a minute or so — the paper appears
					with status <strong>Extracting</strong>, then <strong>Mapped</strong> once its
					questions are linked to topics.
				</p>

				<ErrorMessage :message="errorText" />
			</div>
		</template>
		<template #actions>
			<Button variant="solid" :loading="uploading" :disabled="!canSubmit" @click="submit">
				Upload and extract
			</Button>
		</template>
	</Dialog>
</template>
