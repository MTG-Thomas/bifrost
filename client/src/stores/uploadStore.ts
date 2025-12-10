import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";

export interface UploadFailure {
	path: string;
	error: string;
}

export interface UploadState {
	isUploading: boolean;
	isCancelling: boolean;
	isCancelled: boolean;
	currentFile: string | null;
	completedCount: number;
	totalCount: number;
	failures: UploadFailure[];
}

interface UploadStore extends UploadState {
	startUpload: (totalFiles: number) => void;
	updateProgress: (currentFile: string, completedCount: number) => void;
	recordFailure: (path: string, error: string) => void;
	finishUpload: () => void;
	cancelUpload: () => void;
	resetState: () => void;
	shouldContinueUpload: () => boolean;
}

const initialState: UploadState = {
	isUploading: false,
	isCancelling: false,
	isCancelled: false,
	currentFile: null,
	completedCount: 0,
	totalCount: 0,
	failures: [],
};

export const useUploadStore = create<UploadStore>((set, get) => ({
	...initialState,

	startUpload: (totalFiles: number) => {
		set({
			isUploading: true,
			isCancelling: false,
			isCancelled: false,
			currentFile: null,
			completedCount: 0,
			totalCount: totalFiles,
			failures: [],
		});
	},

	updateProgress: (currentFile: string, completedCount: number) => {
		set((state) => ({
			...state,
			currentFile,
			completedCount,
		}));
	},

	recordFailure: (path: string, error: string) => {
		set((state) => ({
			...state,
			failures: [...state.failures, { path, error }],
		}));
	},

	finishUpload: () => {
		set((state) => ({
			...state,
			isUploading: false,
			isCancelling: false,
			currentFile: null,
			completedCount: state.totalCount,
		}));
	},

	cancelUpload: () => {
		set((state) => ({
			...state,
			isCancelling: true,
			isCancelled: true,
		}));
	},

	resetState: () => {
		set(initialState);
	},

	shouldContinueUpload: () => {
		return !get().isCancelled;
	},
}));

/**
 * Convenience hook that matches the old useUploadProgress API for easier migration.
 * Components can use this instead of useUploadStore directly.
 */
export function useUploadProgress() {
	// Use useShallow to prevent infinite re-renders when selecting an object
	const state = useUploadStore(
		useShallow((s) => ({
			isUploading: s.isUploading,
			isCancelling: s.isCancelling,
			currentFile: s.currentFile,
			completedCount: s.completedCount,
			totalCount: s.totalCount,
			failures: s.failures,
		})),
	);

	// Get actions once - they're stable references from zustand
	const actions = useUploadStore.getState();

	return {
		state,
		startUpload: actions.startUpload,
		updateProgress: actions.updateProgress,
		recordFailure: actions.recordFailure,
		finishUpload: actions.finishUpload,
		cancelUpload: actions.cancelUpload,
		resetState: actions.resetState,
		shouldContinueUpload: actions.shouldContinueUpload,
	};
}
