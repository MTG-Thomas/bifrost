import { useCallback, useState, useRef, useEffect } from "react";
import { fileService, type FileMetadata } from "@/services/fileService";
import { useEditorStore } from "@/stores/editorStore";
import { useUploadProgress } from "@/stores/uploadStore";
import { useFileTree } from "@/hooks/useFileTree";
import { isExcludedPath } from "@/lib/file-filter";
import { toast } from "sonner";
import type { components } from "@/lib/v1";

type WorkflowIdConflict = components["schemas"]["WorkflowIdConflict"];

/**
 * Interface for a file with its relative path from the drop target
 */
export interface FileWithPath {
	file: File;
	relativePath: string;
}

/**
 * Recursively traverse a FileSystemEntry to collect all files with their relative paths.
 * Supports both files and folders dropped from the filesystem.
 */
export async function traverseFileSystemEntry(
	entry: FileSystemEntry,
	basePath: string = "",
): Promise<FileWithPath[]> {
	const results: FileWithPath[] = [];

	if (entry.isFile) {
		const fileEntry = entry as FileSystemFileEntry;
		const file = await new Promise<File>((resolve, reject) => {
			fileEntry.file(resolve, reject);
		});
		results.push({
			file,
			relativePath: basePath ? `${basePath}/${entry.name}` : entry.name,
		});
	} else if (entry.isDirectory) {
		const dirEntry = entry as FileSystemDirectoryEntry;
		const reader = dirEntry.createReader();

		// readEntries may return partial results, so we need to loop until empty
		const readAllEntries = async (): Promise<FileSystemEntry[]> => {
			const entries: FileSystemEntry[] = [];
			let batch: FileSystemEntry[];
			do {
				batch = await new Promise((resolve, reject) => {
					reader.readEntries(resolve, reject);
				});
				entries.push(...batch);
			} while (batch.length > 0);
			return entries;
		};

		const entries = await readAllEntries();
		const newBasePath = basePath ? `${basePath}/${entry.name}` : entry.name;

		for (const childEntry of entries) {
			const childResults = await traverseFileSystemEntry(
				childEntry,
				newBasePath,
			);
			results.push(...childResults);
		}
	}

	return results;
}

/**
 * Convert a FileContentResponse to a FileMetadata for optimistic updates
 */
export function responseToMetadata(
	path: string,
	size: number,
	modified: string,
): FileMetadata {
	const name = path.split("/").pop()!;
	const lastDot = name.lastIndexOf(".");
	return {
		path,
		name,
		type: "file",
		size,
		extension: lastDot > 0 ? name.substring(lastDot + 1) : null,
		modified,
		entity_type: null,
		entity_id: null,
	};
}

/**
 * Detect if a file is text-based (can be read as UTF-8)
 */
export function isTextFile(file: File): boolean {
	return (
		file.type.startsWith("text/") ||
		file.type === "application/json" ||
		file.type === "application/javascript" ||
		file.type === "application/xml" ||
		!!file.name.match(
			/\.(txt|md|json|js|jsx|ts|tsx|py|java|c|cpp|h|hpp|css|scss|html|xml|yaml|yml|sh|bash|sql|log|csv)$/i,
		)
	);
}

export type CreatingItemType = "file" | "folder" | null;

export interface UploadConflictState {
	count: number;
	onCancel: () => void;
	onReplaceAll: () => void;
}

export interface UploadWorkflowConflictsState {
	conflicts: WorkflowIdConflict[];
	files: Array<{
		filePath: string;
		content: string;
		encoding: "utf-8" | "base64";
		conflictIds: Record<string, string>;
	}>;
}

/**
 * Hook that encapsulates file tree action handlers:
 * create, rename, delete, drag-and-drop, and file upload operations.
 */
export function useFileTreeActions() {
	const {
		files,
		isLoading,
		isFolderLoading,
		loadFiles,
		toggleFolder,
		isFolderExpanded,
		refreshAll,
		addFilesOptimistically,
		removeFromTree,
	} = useFileTree();
	const {
		startUpload,
		updateProgress,
		recordFailure,
		finishUpload,
		shouldContinueUpload,
	} = useUploadProgress();
	const tabs = useEditorStore((state) => state.tabs);
	const activeTabIndex = useEditorStore((state) => state.activeTabIndex);
	const setOpenFile = useEditorStore((state) => state.setOpenFile);
	const setLoadingFile = useEditorStore((state) => state.setLoadingFile);
	const updateTabPath = useEditorStore((state) => state.updateTabPath);
	const closeTabsByPath = useEditorStore((state) => state.closeTabsByPath);

	// Compute active tab from state
	const activeTab =
		activeTabIndex >= 0 && activeTabIndex < tabs.length
			? tabs[activeTabIndex]
			: null;
	const openFile = activeTab?.file || null;

	// Creating item state
	const [creatingItem, setCreatingItem] = useState<CreatingItemType>(null);
	const [newItemName, setNewItemName] = useState("");
	const [creatingInFolder, setCreatingInFolder] = useState<string | null>(null);

	// Rename state
	const [renamingFile, setRenamingFile] = useState<FileMetadata | null>(null);
	const [renameValue, setRenameValue] = useState("");

	// Delete state
	const [fileToDelete, setFileToDelete] = useState<FileMetadata | null>(null);

	// Drag state
	const [dragOverFolder, setDragOverFolder] = useState<string | null>(null);

	// Processing state
	const [isProcessing, setIsProcessing] = useState(false);

	// Upload conflict state
	const [uploadConflict, setUploadConflict] =
		useState<UploadConflictState | null>(null);

	// Workflow ID conflict state for uploads
	const [uploadWorkflowConflicts, setUploadWorkflowConflicts] =
		useState<UploadWorkflowConflictsState | null>(null);

	const inputRef = useRef<HTMLInputElement>(null);
	const renameInputRef = useRef<HTMLInputElement>(null);

	// Load root directory on mount
	useEffect(() => {
		loadFiles("");
	}, [loadFiles]);

	const handleFileClick = useCallback(
		async (file: FileMetadata) => {
			if (file.type === "folder") {
				toggleFolder(file.path);
				return;
			}

			// Auto-expand parent folders
			try {
				const pathParts = file.path.split("/");
				pathParts.pop(); // Remove filename
				let currentPath = "";
				for (const part of pathParts) {
					currentPath = currentPath ? `${currentPath}/${part}` : part;
					// Load folder contents if not already loaded
					if (!isFolderExpanded(currentPath)) {
						await toggleFolder(currentPath);
					}
				}
			} catch {
				// Ignore folder expansion errors
			}

			// Load file directly - no conflict checking on explicit user click
			try {
				setLoadingFile(true);
				const response = await fileService.readFile(file.path);
				setOpenFile(
					file,
					response.content,
					response.encoding as "utf-8" | "base64",
					response.etag,
				);
			} catch {
				setLoadingFile(false);
			}
		},
		[toggleFolder, isFolderExpanded, setOpenFile, setLoadingFile],
	);

	const handleFolderToggle = useCallback(
		(folder: FileMetadata) => {
			toggleFolder(folder.path);
		},
		[toggleFolder],
	);

	const handleCancelNewItem = useCallback(() => {
		setCreatingItem(null);
		setNewItemName("");
		setCreatingInFolder(null);
	}, []);

	const handleCreateFile = useCallback((folderPath?: string) => {
		setCreatingItem("file");
		setNewItemName("");
		setCreatingInFolder(folderPath || null);
	}, []);

	const handleCreateFolder = useCallback((folderPath?: string) => {
		setCreatingItem("folder");
		setNewItemName("");
		setCreatingInFolder(folderPath || null);
	}, []);

	const handleInputMouseDown = useCallback((e: React.MouseEvent) => {
		e.stopPropagation();
	}, []);

	const handleRefresh = useCallback(async () => {
		await refreshAll();
	}, [refreshAll]);

	const handleSaveNewItem = useCallback(async () => {
		if (!newItemName.trim() || !creatingItem) return;

		try {
			setIsProcessing(true);

			const fullPath = creatingInFolder
				? `${creatingInFolder}/${newItemName}`
				: newItemName;

			if (creatingItem === "file") {
				await fileService.writeFile(fullPath, "");
			} else {
				await fileService.createFolder(fullPath);
			}

			await loadFiles(creatingInFolder || "");

			setCreatingItem(null);
			setNewItemName("");
			setCreatingInFolder(null);
		} catch (err) {
			toast.error(`Failed to create ${creatingItem}`, {
				description: err instanceof Error ? err.message : String(err),
			});
		} finally {
			setIsProcessing(false);
		}
	}, [newItemName, creatingItem, creatingInFolder, loadFiles]);

	// Focus input when creating new item
	useEffect(() => {
		if (creatingItem && inputRef.current) {
			inputRef.current.focus();
		}
	}, [creatingItem, creatingInFolder]);

	// Handle clicks outside the input to cancel if empty
	useEffect(() => {
		if (!creatingItem) return;

		const handleClickOutside = (event: MouseEvent) => {
			if (
				inputRef.current &&
				!inputRef.current.contains(event.target as Node)
			) {
				if (!newItemName.trim()) {
					handleCancelNewItem();
				}
			}
		};

		document.addEventListener("mousedown", handleClickOutside);

		return () => {
			document.removeEventListener("mousedown", handleClickOutside);
		};
	}, [creatingItem, newItemName, handleCancelNewItem]);

	const handleNewItemKeyDown = useCallback(
		(e: React.KeyboardEvent<HTMLInputElement>) => {
			if (e.key === "Enter") {
				e.preventDefault();
				if (newItemName.trim()) {
					handleSaveNewItem();
				} else {
					handleCancelNewItem();
				}
			} else if (e.key === "Escape") {
				e.preventDefault();
				handleCancelNewItem();
			}
		},
		[handleSaveNewItem, handleCancelNewItem, newItemName],
	);

	const handleDelete = useCallback((file: FileMetadata) => {
		setFileToDelete(file);
	}, []);

	const handleConfirmDelete = useCallback(async () => {
		if (!fileToDelete) return;

		const isFolder = fileToDelete.type === "folder";
		const deletePath = fileToDelete.path;
		const deleteName = fileToDelete.name;

		try {
			setIsProcessing(true);

			const closedCount = closeTabsByPath(deletePath, isFolder);

			removeFromTree(deletePath, isFolder);
			setFileToDelete(null);

			await fileService.deletePath(deletePath);

			if (closedCount > 0) {
				toast.success(
					isFolder
						? `Deleted folder ${deleteName} and closed ${closedCount} open ${closedCount === 1 ? "file" : "files"}`
						: `Deleted ${deleteName} and closed the tab`,
				);
			} else {
				toast.success(`Deleted ${deleteName}`);
			}
		} catch (err) {
			const parentFolder = deletePath.includes("/")
				? deletePath.substring(0, deletePath.lastIndexOf("/"))
				: "";
			await loadFiles(parentFolder);
			toast.error("Failed to delete", {
				description: err instanceof Error ? err.message : String(err),
			});
		} finally {
			setIsProcessing(false);
		}
	}, [fileToDelete, loadFiles, closeTabsByPath, removeFromTree]);

	const handleRename = useCallback((file: FileMetadata) => {
		setRenamingFile(file);
		setRenameValue(file.name);
	}, []);

	const handleCancelRename = useCallback(() => {
		setRenamingFile(null);
		setRenameValue("");
	}, []);

	const handleSaveRename = useCallback(async () => {
		if (
			!renamingFile ||
			!renameValue.trim() ||
			renameValue === renamingFile.name
		) {
			handleCancelRename();
			return;
		}

		try {
			setIsProcessing(true);
			const newPath = renamingFile.path.includes("/")
				? renamingFile.path.replace(/[^/]+$/, renameValue)
				: renameValue;

			const parentFolder = renamingFile.path.includes("/")
				? renamingFile.path.substring(
						0,
						renamingFile.path.lastIndexOf("/"),
					)
				: "";

			updateTabPath(renamingFile.path, newPath);

			await fileService.renamePath(renamingFile.path, newPath);
			await loadFiles(parentFolder);
			toast.success(`Renamed to ${renameValue}`);

			handleCancelRename();
		} catch (err) {
			toast.error("Failed to rename", {
				description: err instanceof Error ? err.message : String(err),
			});
		} finally {
			setIsProcessing(false);
		}
	}, [
		renamingFile,
		renameValue,
		loadFiles,
		handleCancelRename,
		updateTabPath,
	]);

	// Focus rename input when renaming starts
	useEffect(() => {
		if (renamingFile && renameInputRef.current) {
			renameInputRef.current.focus();
			const lastDotIndex = renameValue.lastIndexOf(".");
			if (lastDotIndex > 0) {
				renameInputRef.current.setSelectionRange(0, lastDotIndex);
			} else {
				renameInputRef.current.select();
			}
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [renamingFile]);

	// Handle clicks outside the rename input to save
	useEffect(() => {
		if (!renamingFile) return;

		const handleClickOutside = (event: MouseEvent) => {
			if (
				renameInputRef.current &&
				!renameInputRef.current.contains(event.target as Node)
			) {
				if (renameValue.trim()) {
					handleSaveRename();
				} else {
					handleCancelRename();
				}
			}
		};

		document.addEventListener("mousedown", handleClickOutside);

		return () => {
			document.removeEventListener("mousedown", handleClickOutside);
		};
	}, [renamingFile, renameValue, handleSaveRename, handleCancelRename]);

	const handleRenameKeyDown = useCallback(
		(e: React.KeyboardEvent<HTMLInputElement>) => {
			if (e.key === "Enter") {
				e.preventDefault();
				if (renameValue.trim()) {
					handleSaveRename();
				} else {
					handleCancelRename();
				}
			} else if (e.key === "Escape") {
				e.preventDefault();
				handleCancelRename();
			}
		},
		[handleSaveRename, handleCancelRename, renameValue],
	);

	const handleRenameInputMouseDown = useCallback((e: React.MouseEvent) => {
		e.stopPropagation();
	}, []);

	const handleDragStart = useCallback(
		(e: React.DragEvent, file: FileMetadata) => {
			e.dataTransfer.effectAllowed = "move";
			e.dataTransfer.setData("text/plain", file.path);
			e.dataTransfer.setData("application/json", JSON.stringify(file));
		},
		[],
	);

	const handleDragOver = useCallback(
		(e: React.DragEvent, targetFolder?: string) => {
			e.preventDefault();

			const hasFiles = e.dataTransfer.types.includes("Files");
			const hasInternalData = e.dataTransfer.types.includes("text/plain");

			if (hasFiles && !hasInternalData) {
				e.dataTransfer.dropEffect = "copy";
			} else {
				e.dataTransfer.dropEffect = "move";
			}

			setDragOverFolder(targetFolder || "");
		},
		[],
	);

	const handleDragLeave = useCallback(() => {
		setDragOverFolder(null);
	}, []);

	const handleDrop = useCallback(
		async (e: React.DragEvent, targetFolder?: string) => {
			e.preventDefault();
			setDragOverFolder(null);

			const targetPath = targetFolder || "";

			// Check if this is an external file drop
			const items = e.dataTransfer.items;
			const hasExternalFiles =
				items &&
				items.length > 0 &&
				Array.from(items).some((item) => item.kind === "file");

			if (hasExternalFiles) {
				const allFiles: FileWithPath[] = [];

				// Extract all entries synchronously first
				const entries: Array<{
					entry: FileSystemEntry | null;
					fallbackFile: File | null;
				}> = [];

				for (let i = 0; i < items.length; i++) {
					const item = items[i];
					if (item.kind !== "file") continue;
					const entry = item.webkitGetAsEntry?.() ?? null;
					const fallbackFile = item.getAsFile();
					entries.push({ entry, fallbackFile });
				}

				for (const { entry, fallbackFile } of entries) {
					if (entry) {
						if (entry.isDirectory) {
							try {
								const filesFromEntry =
									await traverseFileSystemEntry(entry);
								allFiles.push(...filesFromEntry);
							} catch {
								console.warn(
									`Failed to traverse directory: ${entry.name}`,
								);
							}
						} else if (entry.isFile) {
							try {
								const fileEntry = entry as FileSystemFileEntry;
								const file = await new Promise<File>(
									(resolve, reject) => {
										fileEntry.file(resolve, reject);
									},
								);
								allFiles.push({
									file,
									relativePath: file.name,
								});
							} catch {
								if (fallbackFile) {
									allFiles.push({
										file: fallbackFile,
										relativePath: fallbackFile.name,
									});
								}
							}
						}
					} else if (fallbackFile) {
						allFiles.push({
							file: fallbackFile,
							relativePath: fallbackFile.name,
						});
					}
				}

				if (allFiles.length === 0) return;

				const filteredFiles = allFiles.filter(({ relativePath }) => {
					const fullPath = targetPath
						? `${targetPath}/${relativePath}`
						: relativePath;
					return !isExcludedPath(fullPath);
				});

				const filesToUpload = filteredFiles;
				if (filesToUpload.length === 0) {
					toast.info(
						"No files to upload (all files were filtered out)",
					);
					return;
				}

				// Check for existing files that would be overwritten
				try {
					const existingFiles = await fileService.listFiles(
						targetPath,
						true,
					);
					const existingPaths = new Set(
						existingFiles
							.filter((f) => f.type === "file")
							.map((f) => f.path),
					);

					const conflictCount = filesToUpload.filter(
						({ relativePath }) => {
							const fullPath = targetPath
								? `${targetPath}/${relativePath}`
								: relativePath;
							return existingPaths.has(fullPath);
						},
					).length;

					if (conflictCount > 0) {
						const shouldReplace = await new Promise<boolean>(
							(resolve) => {
								setUploadConflict({
									count: conflictCount,
									onCancel: () => {
										setUploadConflict(null);
										resolve(false);
									},
									onReplaceAll: () => {
										setUploadConflict(null);
										resolve(true);
									},
								});
							},
						);

						if (!shouldReplace) {
							return;
						}
					}
				} catch {
					// If we can't check for existing files, proceed with upload
				}

				startUpload(filesToUpload.length);
				const uploadedFiles: FileMetadata[] = [];

				const allConflicts: Array<{
					conflicts: WorkflowIdConflict[];
					filePath: string;
					content: string;
					encoding: "utf-8" | "base64";
				}> = [];

				for (let i = 0; i < filesToUpload.length; i++) {
					if (!shouldContinueUpload()) {
						toast.info(
							`Upload cancelled (${i} of ${filesToUpload.length} files uploaded)`,
						);
						break;
					}

					const { file, relativePath } = filesToUpload[i];
					const filePath = targetPath
						? `${targetPath}/${relativePath}`
						: relativePath;

					updateProgress(file.name, i);

					try {
						let content: string;
						let encoding: "utf-8" | "base64";

						if (isTextFile(file)) {
							content = await file.text();
							encoding = "utf-8";
						} else {
							const arrayBuffer = await file.arrayBuffer();
							const bytes = new Uint8Array(arrayBuffer);
							const chunkSize = 8192;
							let binary = "";
							for (let i = 0; i < bytes.length; i += chunkSize) {
								const chunk = bytes.subarray(
									i,
									Math.min(i + chunkSize, bytes.length),
								);
								binary += String.fromCharCode(...chunk);
							}
							content = btoa(binary);
							encoding = "base64";
						}

						const response = await fileService.writeFile(
							filePath,
							content,
							encoding,
							undefined,
							true,
						);

						if (
							response.workflow_id_conflicts &&
							response.workflow_id_conflicts.length > 0
						) {
							allConflicts.push({
								conflicts: response.workflow_id_conflicts,
								filePath,
								content,
								encoding,
							});
						}

						const metadata = responseToMetadata(
							response.path,
							response.size,
							response.modified,
						);
						uploadedFiles.push(metadata);
					} catch (err) {
						recordFailure(
							filePath,
							err instanceof Error ? err.message : String(err),
						);
					}
				}

				if (uploadedFiles.length > 0) {
					addFilesOptimistically(uploadedFiles, targetPath);
				}

				finishUpload();

				if (allConflicts.length > 0) {
					const flatConflicts = allConflicts.flatMap(
						(c) => c.conflicts,
					);

					const filesWithConflicts = allConflicts.map((c) => ({
						filePath: c.filePath,
						content: c.content,
						encoding: c.encoding,
						conflictIds: c.conflicts.reduce(
							(acc, conflict) => {
								acc[conflict.function_name] =
									conflict.existing_id;
								return acc;
							},
							{} as Record<string, string>,
						),
					}));

					setUploadWorkflowConflicts({
						conflicts: flatConflicts,
						files: filesWithConflicts,
					});
				}

				return;
			}

			// Internal move operation
			try {
				const draggedPath = e.dataTransfer.getData("text/plain");
				if (!draggedPath) return;

				if (draggedPath === targetFolder) return;

				if (targetFolder && targetFolder.startsWith(draggedPath + "/"))
					return;

				const fileName = draggedPath.split("/").pop()!;
				const newPath = targetFolder
					? `${targetFolder}/${fileName}`
					: fileName;

				if (draggedPath === newPath) return;

				setIsProcessing(true);

				const sourceFolder = draggedPath.includes("/")
					? draggedPath.substring(0, draggedPath.lastIndexOf("/"))
					: "";
				const targetFolderPath = targetFolder || "";

				updateTabPath(draggedPath, newPath);

				await fileService.renamePath(draggedPath, newPath);

				await loadFiles(sourceFolder);
				if (sourceFolder !== targetFolderPath) {
					await loadFiles(targetFolderPath);
				}

				toast.success(`Moved ${fileName}`);
			} catch (err) {
				toast.error("Failed to move", {
					description:
						err instanceof Error ? err.message : String(err),
				});
			} finally {
				setIsProcessing(false);
			}
		},
		[
			loadFiles,
			updateTabPath,
			startUpload,
			updateProgress,
			recordFailure,
			finishUpload,
			addFilesOptimistically,
			shouldContinueUpload,
		],
	);

	return {
		// File tree data
		files,
		isLoading,
		isFolderLoading,
		isFolderExpanded,
		openFile,

		// Create actions
		creatingItem,
		creatingInFolder,
		newItemName,
		setNewItemName,
		inputRef,
		handleCreateFile,
		handleCreateFolder,
		handleCancelNewItem,
		handleSaveNewItem,
		handleNewItemKeyDown,
		handleInputMouseDown,

		// File/folder actions
		handleFileClick,
		handleFolderToggle,
		handleRefresh,

		// Delete actions
		fileToDelete,
		setFileToDelete,
		handleDelete,
		handleConfirmDelete,

		// Rename actions
		renamingFile,
		renameValue,
		setRenameValue,
		renameInputRef,
		handleRename,
		handleCancelRename,
		handleSaveRename,
		handleRenameKeyDown,
		handleRenameInputMouseDown,

		// Drag and drop
		dragOverFolder,
		handleDragStart,
		handleDragOver,
		handleDragLeave,
		handleDrop,

		// Processing state
		isProcessing,

		// Upload conflicts
		uploadConflict,
		uploadWorkflowConflicts,
		setUploadWorkflowConflicts,
	};
}
