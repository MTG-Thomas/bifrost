/**
 * SyncDiffView - Shows a readonly diff for sync preview
 *
 * Displays local vs remote content using Monaco DiffEditor.
 * For uncommitted changes: "Working Tree" vs "Last Commit"
 * For merge conflicts: "Ours (Platform)" vs "Theirs (Git)"
 */

import { useRef, useCallback } from "react";
import { DiffEditor, type DiffOnMount } from "@monaco-editor/react";
import { useTheme } from "@/contexts/ThemeContext";
import type * as Monaco from "monaco-editor";
import { Button } from "@/components/ui/button";
import { X, FileText, Bot, AppWindow, Workflow, FileCode, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useEditorStore, type DiffPreviewState } from "@/stores/editorStore";

/** Icon mapping for entity types */
const ENTITY_ICONS = {
	form: { icon: FileText, className: "text-green-500" },
	agent: { icon: Bot, className: "text-orange-500" },
	app: { icon: AppWindow, className: "text-purple-500" },
	workflow: { icon: Workflow, className: "text-blue-500" },
	app_file: { icon: FileCode, className: "text-gray-500" },
} as const;

interface SyncDiffViewProps {
	preview: DiffPreviewState;
}

export function SyncDiffView({ preview }: SyncDiffViewProps) {
	// Use loading state from preview (set by SourceControlPanel during fetch)
	const isLoading = preview.isLoading ?? false;
	const { theme } = useTheme();
	const editorRef = useRef<Monaco.editor.IStandaloneDiffEditor | null>(null);
	const clearDiffPreview = useEditorStore((state) => state.clearDiffPreview);

	// Store editor reference on mount
	// Note: We don't manually dispose - Monaco's DiffEditor component handles its own lifecycle.
	// Manual disposal causes "TextModel got disposed before DiffEditorWidget model got reset"
	// when rapidly switching between items because models are disposed while still referenced.
	const handleMount: DiffOnMount = useCallback((editor) => {
		editorRef.current = editor;
	}, []);

	// Get entity icon
	const entityType = preview.entityType as keyof typeof ENTITY_ICONS | null;
	const iconConfig = entityType ? ENTITY_ICONS[entityType] : null;
	const IconComponent = iconConfig?.icon ?? FileCode;
	const iconClassName = iconConfig?.className ?? "text-gray-500";

	// Determine language from path
	const getLanguage = (path: string): string => {
		if (path.endsWith(".json")) return "json";
		if (path.endsWith(".py")) return "python";
		if (path.endsWith(".tsx") || path.endsWith(".ts")) return "typescript";
		if (path.endsWith(".jsx") || path.endsWith(".js")) return "javascript";
		if (path.endsWith(".yaml") || path.endsWith(".yml")) return "yaml";
		return "plaintext";
	};

	return (
		<div className="flex flex-col h-full bg-background">
			{/* Header */}
			<div className="flex items-center justify-between p-3 border-b bg-muted/30">
				<div className="flex items-center gap-2">
					<IconComponent className={cn("h-5 w-5", iconClassName)} />
					<div>
						<h3 className="text-sm font-semibold">{preview.displayName}</h3>
						<p className="text-xs text-muted-foreground">{preview.path}</p>
					</div>
				</div>
				<Button
					variant="ghost"
					size="icon"
					className="h-6 w-6"
					onClick={clearDiffPreview}
					title="Close diff view"
				>
					<X className="h-4 w-4" />
				</Button>
			</div>

			{/* Diff editor or loading state */}
			<div className="flex-1 min-h-0">
				{isLoading ? (
					<div className="flex h-full items-center justify-center">
						<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
					</div>
				) : (
					<DiffEditor
						height="100%"
						language={getLanguage(preview.path)}
						theme={theme === "dark" ? "vs-dark" : "light"}
						original={preview.remoteContent ?? ""}
						modified={preview.localContent ?? ""}
						onMount={handleMount}
						options={{
							readOnly: true,
							minimap: { enabled: false },
							scrollBeyondLastLine: false,
							renderSideBySide: false,
						}}
					/>
				)}
			</div>

			{/* Resolution buttons for conflicts */}
			{preview.isConflict && preview.onResolve && (
				<div className="flex items-center justify-end gap-2 p-3 border-t bg-muted/30">
					<Button
						variant="outline"
						size="sm"
						onClick={() => preview.onResolve?.("ours")}
						className={cn(
							preview.resolution === "ours" && "bg-blue-500 text-white"
						)}
					>
						Keep Ours
					</Button>
					<Button
						variant="outline"
						size="sm"
						onClick={() => preview.onResolve?.("theirs")}
						className={cn(
							preview.resolution === "theirs" && "bg-blue-500 text-white"
						)}
					>
						Keep Theirs
					</Button>
				</div>
			)}
		</div>
	);
}
