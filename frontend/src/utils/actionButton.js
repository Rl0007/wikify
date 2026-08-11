// A labelled header action shrinks to an icon-only button on phones — header rows run
// out of width long before the label stops being useful, and PageHeader never wraps.
// The tooltip/aria-label keeps the action named for pointer and screen-reader users.
export function actionButtonProps(compact, icon, label) {
	if (compact) return { icon, tooltip: label, "aria-label": label };
	return { iconLeft: icon, label };
}
