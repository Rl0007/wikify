// Data layer for /exam-analysis — thin wrappers over the `wikify.api.exam.*` contract.
// Mirrors useRag.js: every call degrades to `errorText` rather than throwing, because the
// backend may not be migrated on the site the SPA is talking to.
import { computed, ref } from "vue";
import { useCall } from "frappe-ui";

function errorMessage(error) {
	if (!error) return "";
	const text = error.messages?.[0] || error.message || String(error);
	if (text === "Failed to fetch") {
		return "Couldn’t reach the server — the request didn’t complete. Try again.";
	}
	return text.replace(/^[A-Za-z]*Error:\s*/, "");
}

export function useHeatmap() {
	const project = ref(null);
	// Which Source Documents the analysis is scoped to. Empty means every source in the
	// project — the "full-blown" mode. A subset answers the narrower question: what does
	// THIS material alone prepare me for, and what does it leave uncovered?
	const scope = ref([]);
	const sourcesCall = useCall({
		url: "/api/v2/method/wikify.api.exam.sources",
		method: "GET",
		immediate: false,
	});
	const heatmapCall = useCall({
		url: "/api/v2/method/wikify.api.exam.heatmap",
		method: "GET",
		immediate: false,
	});
	const papersCall = useCall({
		url: "/api/v2/method/wikify.api.exam.papers",
		method: "GET",
		immediate: false,
	});

	async function load(name) {
		if (!name) return;
		project.value = name;
		await Promise.all([
			heatmapCall.submit({
				project: name,
				// Sent as JSON because a GET carries it as text; the endpoint parses it back.
				sources: scope.value.length ? JSON.stringify(scope.value) : null,
			}),
			papersCall.submit({ project: name }),
			sourcesCall.submit({ project: name }),
		]);
	}

	async function setScope(names) {
		scope.value = names;
		await load(project.value);
	}

	const grid = computed(() => heatmapCall.data || null);
	const topics = computed(() => grid.value?.topics || []);
	const years = computed(() => grid.value?.years || []);
	const papers = computed(() => papersCall.data || []);
	const totals = computed(() => grid.value?.totals || {});
	const errorText = computed(
		() => errorMessage(heatmapCall.error) || errorMessage(papersCall.error),
	);

	// The busiest single cell sets the shading scale. Taken from the data rather than fixed,
	// because a corpus with one dominant chapter and one with an even spread should both
	// use the full range instead of most cells sitting at the pale end.
	const peak = computed(() => {
		const cells = grid.value?.cells || {};
		return Object.values(cells).reduce((max, cell) => Math.max(max, cell.marks || 0), 0);
	});

	function cell(topic, year) {
		return grid.value?.cells?.[`${topic}|${year}`] || null;
	}

	return {
		project,
		scope,
		setScope,
		availableSources: computed(() => sourcesCall.data || []),
		load,
		grid,
		topics,
		years,
		papers,
		totals,
		peak,
		cell,
		errorText,
		loading: computed(() => heatmapCall.loading || papersCall.loading),
	};
}

export function useTopicDrilldown() {
	const open = ref(false);
	const topic = ref("");
	const year = ref(null);
	const call = useCall({
		url: "/api/v2/method/wikify.api.exam.topic_questions",
		method: "GET",
		immediate: false,
	});

	async function show(project, topicKey, cellYear) {
		topic.value = topicKey;
		year.value = cellYear || null;
		open.value = true;
		await call.submit({ project, topic: topicKey, year: cellYear || null });
	}

	return {
		open,
		topic,
		year,
		show,
		questions: computed(() => call.data?.questions || []),
		sections: computed(() => call.data?.sections || []),
		loading: computed(() => call.loading),
		errorText: computed(() => errorMessage(call.error)),
	};
}

export function useUploadPaper() {
	const uploadCall = useCall({
		url: "/api/v2/method/wikify.api.exam.upload_paper",
		method: "POST",
		immediate: false,
	});

	async function upload(payload) {
		const created = await uploadCall.submit(payload);
		return uploadCall.error ? null : created;
	}

	return {
		upload,
		uploading: computed(() => uploadCall.loading),
		errorText: computed(() => errorMessage(uploadCall.error)),
	};
}

// The three lifecycle actions a paper needs after upload: throw it away, run extraction
// again after a bad parse, and recompute the whole project's topic mapping. Grouped in one
// composable because the page always needs all three together.
export function usePaperActions() {
	const deleteCall = useCall({
		url: "/api/v2/method/wikify.api.exam.delete_paper",
		method: "POST",
		immediate: false,
	});
	const reextractCall = useCall({
		url: "/api/v2/method/wikify.api.exam.reextract_paper",
		method: "POST",
		immediate: false,
	});
	const remapCall = useCall({
		url: "/api/v2/method/wikify.api.exam.map_questions",
		method: "POST",
		immediate: false,
	});

	return {
		remove: async (paper) => {
			await deleteCall.submit({ paper });
			return !deleteCall.error;
		},
		reextract: async (paper) => {
			await reextractCall.submit({ paper });
			return !reextractCall.error;
		},
		remap: async (project) => {
			const result = await remapCall.submit({ project });
			return remapCall.error ? null : result;
		},
		busy: computed(() => deleteCall.loading || reextractCall.loading || remapCall.loading),
		remapping: computed(() => remapCall.loading),
		errorText: computed(
			() =>
				errorMessage(deleteCall.error) ||
				errorMessage(reextractCall.error) ||
				errorMessage(remapCall.error),
		),
	};
}
