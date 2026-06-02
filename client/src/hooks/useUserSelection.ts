import { useCallback, useMemo, useRef, useState } from "react";

/**
 * Item shape this hook needs. Anything with an `id` works — typed loose so
 * we don't drag the User schema through every test.
 */
export interface SelectableItem {
	id: string;
}

export interface UserSelection<T extends SelectableItem> {
	/** Set of selected ids — reference identity changes on every mutation. */
	selected: Set<string>;
	/** Count of selected items currently in the visible list. */
	count: number;
	/** True when every visible row is selected. */
	allVisibleSelected: boolean;
	/** True when at least one (but not all) visible rows are selected. */
	someVisibleSelected: boolean;
	/** True if id is selected (just a Set.has wrapper, here for stable callers). */
	isSelected: (id: string) => boolean;
	/** Toggle one id. Shift-click range selection extends from last toggle. */
	toggle: (id: string, opts?: { shiftKey?: boolean }) => void;
	/** Select-all-visible toggle. If all visible are selected → clears them; else adds all visible. */
	toggleAllVisible: () => void;
	/** Clear the entire selection. */
	clear: () => void;
	/** Array form of selection, ordered by `items` order (for predictable submission). */
	selectedItems: T[];
}

function pruneToVisible(selected: Set<string>, visibleIdSet: Set<string>) {
	const out = new Set<string>();
	for (const id of selected) {
		if (visibleIdSet.has(id)) out.add(id);
	}
	return out;
}

function applyRangeSelection(
	next: Set<string>,
	visibleIds: string[],
	disabledSet: Set<string>,
	from: number,
	to: number,
	willSelect: boolean,
) {
	for (let i = from; i <= to; i++) {
		const rid = visibleIds[i];
		if (disabledSet.has(rid)) continue;
		if (willSelect) next.add(rid);
		else next.delete(rid);
	}
}

function visibleRange(
	visibleIds: string[],
	lastId: string | null,
	currentId: string,
): [number, number] | null {
	if (!lastId) return null;
	const lastIdx = visibleIds.indexOf(lastId);
	const curIdx = visibleIds.indexOf(currentId);
	if (lastIdx === -1 || curIdx === -1) return null;
	return lastIdx <= curIdx ? [lastIdx, curIdx] : [curIdx, lastIdx];
}

function toggleOne(next: Set<string>, id: string) {
	if (next.has(id)) next.delete(id);
	else next.add(id);
}

/**
 * Selection state for tabular UIs. Keeps a `Set<string>` and prunes it when the
 * underlying list changes (e.g. filter narrows). Re-selection on filter reversion
 * is out of scope — that's a v2.
 *
 * `disabledIds` removes ids from selectability (e.g. self, system user). Toggling
 * a disabled id is a no-op; toggleAllVisible skips disabled rows.
 */
export function useUserSelection<T extends SelectableItem>(
	items: T[],
	disabledIds?: Iterable<string>,
): UserSelection<T> {
	const [rawSelected, setRawSelected] = useState<Set<string>>(() => new Set());
	const lastToggledRef = useRef<string | null>(null);

	const disabledSet = useMemo(
		() => new Set(disabledIds ?? []),
		[disabledIds],
	);

	const visibleIds = useMemo(() => items.map((i) => i.id), [items]);
	const visibleIdSet = useMemo(() => new Set(visibleIds), [visibleIds]);
	const selectableVisibleIds = useMemo(
		() => visibleIds.filter((id) => !disabledSet.has(id)),
		[visibleIds, disabledSet],
	);

	// Pruned view of the selection: only ids that are currently visible "count".
	// Stale ids in rawSelected don't appear in `selected` / `count` / `selectedItems`.
	// They never resurface because every mutation (toggle, toggleAllVisible, clear)
	// derives its next state from this pruned set rather than rawSelected.
	const selected = useMemo(() => {
		return pruneToVisible(rawSelected, visibleIdSet);
	}, [rawSelected, visibleIdSet]);

	const isSelected = useCallback((id: string) => selected.has(id), [selected]);

	const toggle = useCallback(
		(id: string, opts?: { shiftKey?: boolean }) => {
			if (disabledSet.has(id)) return;
			setRawSelected((prev) => {
				// Build "next" from the pruned view so stale ids never bleed back in.
				const next = pruneToVisible(prev, visibleIdSet);
				const willSelect = !next.has(id);
				const range = opts?.shiftKey
					? visibleRange(visibleIds, lastToggledRef.current, id)
					: null;
				if (range) {
					applyRangeSelection(next, visibleIds, disabledSet, range[0], range[1], willSelect);
				} else {
					toggleOne(next, id);
				}
				lastToggledRef.current = id;
				return next;
			});
		},
		[disabledSet, visibleIds, visibleIdSet],
	);

	const toggleAllVisible = useCallback(() => {
		setRawSelected((prev) => {
			const next = pruneToVisible(prev, visibleIdSet);
			const allSelected = selectableVisibleIds.every((id) => next.has(id));
			if (allSelected) {
				for (const id of selectableVisibleIds) next.delete(id);
			} else {
				for (const id of selectableVisibleIds) next.add(id);
			}
			return next;
		});
	}, [selectableVisibleIds, visibleIdSet]);

	const clear = useCallback(() => {
		setRawSelected(new Set());
		lastToggledRef.current = null;
	}, []);

	const allVisibleSelected =
		selectableVisibleIds.length > 0 &&
		selectableVisibleIds.every((id) => selected.has(id));
	const someVisibleSelected =
		!allVisibleSelected && selectableVisibleIds.some((id) => selected.has(id));

	const selectedItems = useMemo(
		() => items.filter((i) => selected.has(i.id)),
		[items, selected],
	);

	return {
		selected,
		count: selectedItems.length,
		allVisibleSelected,
		someVisibleSelected,
		isSelected,
		toggle,
		toggleAllVisible,
		clear,
		selectedItems,
	};
}
