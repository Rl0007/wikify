// Reactive `matchMedia` for layout decisions JS has to make (Splitpanes is a component,
// not a CSS rule, so a media query alone can't collapse a split). One MediaQueryList per
// query string is shared and reference-counted, since several screens ask for the same
// breakpoint at once.
import { onScopeDispose, ref } from "vue";

const queries = new Map();

export function useMediaQuery(query) {
	let entry = queries.get(query);
	if (!entry) {
		const list = window.matchMedia(query);
		const matches = ref(list.matches);
		const onChange = (event) => (matches.value = event.matches);
		list.addEventListener("change", onChange);
		entry = { matches, list, onChange, users: 0 };
		queries.set(query, entry);
	}
	entry.users += 1;
	onScopeDispose(() => {
		entry.users -= 1;
		if (entry.users > 0) return;
		entry.list.removeEventListener("change", entry.onChange);
		queries.delete(query);
	});
	return entry.matches;
}

// The app-wide "a side-by-side split no longer fits" breakpoint. Below it (phones and
// tablet portrait, where the desktop sidebar already eats ~220px) a list∥detail split
// would leave both panes too narrow to read, so those screens drill down instead.
export function useIsNarrow() {
	return useMediaQuery("(max-width: 1023px)");
}
