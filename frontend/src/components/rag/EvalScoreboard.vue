<script setup>
// Recall@k per golden question. There is no eval endpoint yet, so this renders an
// explicitly-labelled placeholder until a later slice wires it up.
</script>

<template>
	<section class="rounded-lg border border-outline-gray-2 bg-surface-elevation-1">
		<header class="flex flex-wrap items-center gap-2 border-b border-outline-gray-2 px-4 py-3">
			<span class="lucide-target size-4 text-ink-gray-5" aria-hidden="true" />
			<h2 class="text-base font-medium text-ink-gray-9">Eval scoreboard</h2>
			<span class="text-xs text-ink-gray-5">recall@k per golden question</span>
		</header>

		<div class="flex flex-col items-center gap-2 px-4 py-10 text-center">
			<span class="lucide-flask-conical size-6 text-ink-gray-4" aria-hidden="true" />
			<p class="text-base text-ink-gray-7">Not wired up yet</p>
			<p class="max-w-md text-sm text-ink-gray-5">
				A later slice adds the golden-question eval endpoint; this board reads it and plots
				recall@k per question — a true proportion, so it gets a real bar.
			</p>
		</div>
	</section>
</template>
