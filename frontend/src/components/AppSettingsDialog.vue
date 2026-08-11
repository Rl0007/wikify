<script setup>
import { computed, ref, watch } from "vue";
import {
	Button,
	ErrorMessage,
	FormControl,
	SettingsBody,
	SettingsContent,
	SettingsDialog,
	SettingsHeader,
	SettingsNavGroup,
	SettingsNavItem,
	SettingsPanel,
	SettingsRow,
	SettingsSidebar,
	Switch,
	toast,
	useCall,
	useDoc,
} from "frappe-ui";

const open = defineModel("open", { type: Boolean, default: false });

// Wikify Settings is a Single — name equals the doctype. System Manager only (enforced
// by the doctype's permissions), so this dialog leans on the standard document API.
const SINGLE = "Wikify Settings";
const settings = useDoc({ doctype: SINGLE, name: SINGLE });

// Save via the standard v2 document-update endpoint (a full doc.save() on the server, so
// the Password field encrypts correctly). useDoc's own setValue is wired to the same
// route but doesn't fire in this frappe-ui build, so we drive it explicitly.
const save = useCall({
	url: `/api/v2/document/${SINGLE}/${SINGLE}`,
	method: "PUT",
	immediate: false,
});

// The OpenRouter key is a Password field: the standard API returns it masked ("*****"),
// never the real secret. So we keep the input blank, show a "saved" hint when a value
// exists, and only write the key when the user actually types a new one.
const keyIsSet = computed(() => !!settings.doc?.openrouter_api_key);
const newKey = ref("");

const MODEL_FIELDS = [
	{ key: "agent_model", label: "Agent model", hint: "Assistant model (projects can override)." },
	{
		key: "vlm_model",
		label: "VLM model",
		hint: "Vision model for re-parsing flagged / visual pages.",
	},
	{
		key: "cleanup_model",
		label: "Cleanup model",
		hint: "Cheap text model for the markdown cleanup pass.",
	},
	{
		key: "judge_model",
		label: "Judge model",
		hint: "Independent grader — keep distinct from the parser.",
	},
	{ key: "classifier_model", label: "Classifier model", hint: "" },
];

const FLOAT_FIELDS = [
	{ key: "pass_threshold", label: "Pass threshold" },
	{ key: "escalate_threshold", label: "Escalate threshold" },
	{ key: "cleanup_recall_tolerance", label: "Cleanup recall tolerance" },
];

const INT_FIELDS = [
	{ key: "render_dpi", label: "Render DPI" },
	{ key: "visual_min_chars", label: "Visual min chars" },
	{ key: "visual_min_drawings", label: "Visual min drawings" },
	{ key: "remediation_workers", label: "Remediation workers" },
	{ key: "classify_workers", label: "Classify workers" },
];

// Local editable copy, seeded once the doc arrives (and re-seeded after a save reload).
const form = ref({});

watch(
	() => settings.doc,
	(doc) => {
		if (!doc) return;
		const next = {};
		for (const f of MODEL_FIELDS) next[f.key] = doc[f.key] ?? "";
		for (const f of FLOAT_FIELDS) next[f.key] = doc[f.key] ?? "";
		for (const f of INT_FIELDS) next[f.key] = doc[f.key] ?? "";
		next.judge_all_pages = !!doc.judge_all_pages;
		form.value = next;
		newKey.value = "";
	},
	{ immediate: true }
);

async function submit() {
	// Build the field map for the standard document-update API. The key is omitted unless
	// the user typed one, so an empty save never clobbers the stored secret.
	const fields = { ...form.value, judge_all_pages: form.value.judge_all_pages ? 1 : 0 };
	if (newKey.value.trim()) fields.openrouter_api_key = newKey.value.trim();
	// submit() resolves (it doesn't throw) on a server error — it sets save.error.
	await save.submit(fields);
	if (save.error) return;
	await settings.reload();
	toast.success("Settings saved");
}

const TABS = [
	{ value: "openrouter", label: "OpenRouter", icon: "lucide-key-round" },
	{ value: "models", label: "Models", icon: "lucide-cpu" },
	{ value: "scoring", label: "Scoring", icon: "lucide-gauge" },
];
const activeTab = ref(TABS[0].value);
</script>

<template>
	<!-- One form spans all panels, so keep hidden panels mounted to preserve edits. -->
	<SettingsDialog v-model="open" v-model:tab="activeTab" :unmount-on-hide="false">
		<SettingsSidebar>
			<SettingsNavGroup label="Wikify">
				<SettingsNavItem v-for="tab in TABS" :key="tab.value" :value="tab.value">
					<template #prefix>
						<span
							:class="[tab.icon, 'size-4 shrink-0 text-ink-gray-6']"
							aria-hidden="true"
						/>
					</template>
					{{ tab.label }}
				</SettingsNavItem>
			</SettingsNavGroup>
		</SettingsSidebar>

		<SettingsContent>
			<SettingsPanel value="openrouter">
				<SettingsHeader title="OpenRouter" description="API access for all AI steps.">
					<template #actions>
						<Button
							variant="solid"
							theme="gray"
							label="Save"
							:loading="save.loading"
							:disabled="!settings.doc"
							@click="submit"
						/>
					</template>
				</SettingsHeader>
				<SettingsBody>
					<ErrorMessage class="mb-4" :message="save.error?.message" />
					<FormControl
						v-model="newKey"
						label="OpenRouter API key"
						type="password"
						:placeholder="keyIsSet ? '•••• key saved — type to replace' : 'sk-or-v1-…'"
					/>
					<p class="mt-1.5 text-p-sm text-ink-gray-5">
						Used for all AI steps. Stored encrypted. Leave blank to keep the current
						key; falls back to site config / the app .env when never set.
					</p>
				</SettingsBody>
			</SettingsPanel>

			<SettingsPanel value="models">
				<SettingsHeader
					title="Models"
					description="OpenRouter model ids per pipeline step."
				>
					<template #actions>
						<Button
							variant="solid"
							theme="gray"
							label="Save"
							:loading="save.loading"
							:disabled="!settings.doc"
							@click="submit"
						/>
					</template>
				</SettingsHeader>
				<SettingsBody>
					<ErrorMessage class="mb-4" :message="save.error?.message" />
					<div class="flex flex-col gap-4">
						<div v-for="f in MODEL_FIELDS" :key="f.key">
							<FormControl
								v-model="form[f.key]"
								:label="f.label"
								type="text"
								placeholder="e.g. anthropic/claude-sonnet-4.6"
							/>
							<p v-if="f.hint" class="mt-1.5 text-p-sm text-ink-gray-5">
								{{ f.hint }}
							</p>
						</div>
					</div>
				</SettingsBody>
			</SettingsPanel>

			<SettingsPanel value="scoring">
				<SettingsHeader
					title="Scoring & thresholds"
					description="Judge gates and parse tuning."
				>
					<template #actions>
						<Button
							variant="solid"
							theme="gray"
							label="Save"
							:loading="save.loading"
							:disabled="!settings.doc"
							@click="submit"
						/>
					</template>
				</SettingsHeader>
				<SettingsBody>
					<ErrorMessage class="mb-4" :message="save.error?.message" />
					<div class="divide-y divide-outline-gray-1">
						<SettingsRow
							title="Judge all pages"
							description="Visual pages are always judged; costs more."
						>
							<Switch v-model="form.judge_all_pages" />
						</SettingsRow>
						<SettingsRow v-for="f in FLOAT_FIELDS" :key="f.key" :title="f.label">
							<FormControl
								v-model="form[f.key]"
								type="number"
								step="0.001"
								class="w-28"
							/>
						</SettingsRow>
						<SettingsRow v-for="f in INT_FIELDS" :key="f.key" :title="f.label">
							<FormControl v-model="form[f.key]" type="number" class="w-28" />
						</SettingsRow>
					</div>
				</SettingsBody>
			</SettingsPanel>
		</SettingsContent>
	</SettingsDialog>
</template>
