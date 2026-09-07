// The single upload path behind both batch surfaces — the drop zone on the document
// list and the multi-select dialog. frappe-ui's <FileUploader> is click-only and
// single-file, so we drive its underlying `useFileUpload()` helper directly: one
// instance per queued file, a small concurrency cap, then one `start_imports` call.
import { computed, ref } from "vue";
import { call, toast, useFileUpload } from "frappe-ui";

// Parallel XHRs. A 20-file drop shouldn't open 20 sockets at once.
const CONCURRENCY = 3;

let seq = 0;

function isPdf(file) {
	return file.type === "application/pdf" || /\.pdf$/i.test(file.name || "");
}

// Same rule the single-file dialog has always used for its default title.
function titleFor(file) {
	return (file.name || "").replace(/\.pdf$/i, "");
}

export function usePdfUpload() {
	// { id, name, title, status: pending|uploading|uploaded|error, progress, file_url, error, file }
	const rows = ref([]);
	// In-flight `upload()` passes; see the re-entry note there.
	let runs = 0;
	const uploading = ref(false);
	const starting = ref(false);
	// Last `start_imports` failure — surfaced inline by the dialog, toasted by the list.
	const error = ref("");

	const uploaded = computed(() => rows.value.filter((r) => r.status === "uploaded"));
	const failed = computed(() => rows.value.filter((r) => r.status === "error"));
	const busy = computed(() => uploading.value || starting.value);

	/** Queue `File`s from an <input multiple> or a drop event. Non-PDFs are dropped. */
	function addFiles(fileList) {
		const files = Array.from(fileList || []);
		const pdfs = files.filter(isPdf);
		const skipped = files.length - pdfs.length;
		if (skipped) {
			toast.warning(
				skipped === 1
					? `Skipped ${files.find((f) => !isPdf(f))?.name} — not a PDF`
					: `Skipped ${skipped} files — only PDFs can be imported`
			);
		}
		for (const file of pdfs) {
			rows.value.push({
				id: ++seq,
				name: file.name,
				title: titleFor(file),
				status: "pending",
				progress: 0,
				file_url: null,
				error: "",
				file,
			});
		}
		return pdfs.length;
	}

	function removeRow(id) {
		rows.value = rows.value.filter((r) => r.id !== id);
	}

	async function uploadRow(row) {
		const uploader = useFileUpload();
		row.status = "uploading";
		row.progress = 0;
		row.error = "";
		try {
			const file = await uploader.upload(row.file, {
				private: true,
				onProgress: ({ percent }) => {
					row.progress = percent;
				},
			});
			row.file_url = file.file_url;
			row.progress = 100;
			row.status = "uploaded";
		} catch (e) {
			row.status = "error";
			row.error = e?.message || "Upload failed";
		}
	}

	/** Upload every pending/errored row, `CONCURRENCY` at a time. Never rejects — a
	 *  failed row is marked and left in the queue for retry. */
	async function upload() {
		const pending = rows.value.filter((r) => r.status === "pending" || r.status === "error");
		if (!pending.length) return;
		// A retry can start a second pass while the first is still running — only the
		// last one out flips `uploading` back off.
		runs += 1;
		uploading.value = true;
		try {
			let next = 0;
			const worker = async () => {
				while (next < pending.length) {
					await uploadRow(pending[next++]);
				}
			};
			await Promise.all(
				Array.from({ length: Math.min(CONCURRENCY, pending.length) }, worker)
			);
		} finally {
			runs -= 1;
			if (runs === 0) uploading.value = false;
		}
	}

	/** Create + enqueue an Import per uploaded row. Returns the new names, or [] with
	 *  `error` set on failure. Errored uploads are left behind for a retry. */
	async function start(project) {
		const files = uploaded.value.map((r) => ({ file_url: r.file_url, title: r.title }));
		if (!files.length) return [];
		starting.value = true;
		error.value = "";
		try {
			const names = await call("wikify.api.imports.start_imports", { files, project });
			// Drop the rows we just handed over; failures stay for retry.
			rows.value = rows.value.filter((r) => r.status !== "uploaded");
			return names || [];
		} catch (e) {
			error.value = e?.messages?.[0] || e?.message || "Could not start the import";
			return [];
		} finally {
			starting.value = false;
		}
	}

	/** Upload then start, the drop-zone one-shot. */
	async function uploadAndStart(project) {
		await upload();
		return start(project);
	}

	function reset() {
		rows.value = [];
		runs = 0;
		uploading.value = false;
		starting.value = false;
		error.value = "";
	}

	return {
		rows,
		uploaded,
		failed,
		uploading,
		starting,
		busy,
		error,
		addFiles,
		removeRow,
		upload,
		start,
		uploadAndStart,
		reset,
	};
}
