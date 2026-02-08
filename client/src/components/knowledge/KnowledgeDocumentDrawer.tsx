/**
 * Knowledge Document Drawer
 *
 * Sheet component for viewing, editing, and creating documents.
 * Uses the TiptapEditor for rich markdown editing.
 */

import { useState, useEffect, useCallback } from "react";
import { Pencil, Save, X } from "lucide-react";
import {
	Sheet,
	SheetContent,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { TiptapEditor } from "@/components/ui/tiptap-editor";
import { toast } from "sonner";
import { authFetch } from "@/lib/auth";

interface KnowledgeDocumentDrawerProps {
	sourceId: string;
	documentId: string | null;
	isCreating: boolean;
	onClose: () => void;
}

interface DocumentFull {
	id: string;
	namespace: string;
	key: string | null;
	content: string;
	metadata: Record<string, unknown>;
	created_at: string | null;
	updated_at: string | null;
}

export function KnowledgeDocumentDrawer({
	sourceId,
	documentId,
	isCreating,
	onClose,
}: KnowledgeDocumentDrawerProps) {
	const [document, setDocument] = useState<DocumentFull | null>(null);
	const [isEditing, setIsEditing] = useState(false);
	const [content, setContent] = useState("");
	const [key, setKey] = useState("");
	const [isSaving, setIsSaving] = useState(false);

	const isOpen = !!documentId || isCreating;

	const loadDocument = useCallback(async () => {
		if (!documentId) return;
		try {
			const response = await authFetch(
				`/api/knowledge-sources/${sourceId}/documents/${documentId}`,
			);
			if (response.ok) {
				const data = await response.json();
				setDocument(data);
				setContent(data.content);
				setKey(data.key || "");
			}
		} catch {
			toast.error("Failed to load document");
		}
	}, [documentId, sourceId]);

	useEffect(() => {
		if (documentId) {
			loadDocument();
			setIsEditing(false);
		} else if (isCreating) {
			setDocument(null);
			setContent("");
			setKey("");
			setIsEditing(true);
		}
	}, [documentId, isCreating, loadDocument]);

	const handleSave = async () => {
		if (!content.trim()) {
			toast.error("Content is required");
			return;
		}

		setIsSaving(true);
		try {
			if (isCreating) {
				const response = await authFetch(`/api/knowledge-sources/${sourceId}/documents`, {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({
						content: content.trim(),
						key: key.trim() || null,
						metadata: {},
					}),
				});
				if (response.ok) {
					toast.success("Document created");
					onClose();
				} else {
					const err = await response.json().catch(() => ({}));
					toast.error(err.detail || "Failed to create document");
				}
			} else if (documentId) {
				const response = await authFetch(
					`/api/knowledge-sources/${sourceId}/documents/${documentId}`,
					{
						method: "PUT",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({
							content: content.trim(),
							metadata: document?.metadata || {},
						}),
					},
				);
				if (response.ok) {
					toast.success("Document updated");
					setIsEditing(false);
					loadDocument();
				} else {
					const err = await response.json().catch(() => ({}));
					toast.error(err.detail || "Failed to update document");
				}
			}
		} catch {
			toast.error("Failed to save document");
		} finally {
			setIsSaving(false);
		}
	};

	return (
		<Sheet open={isOpen} onOpenChange={() => onClose()}>
			<SheetContent className="sm:max-w-[600px] flex flex-col">
				<SheetHeader>
					<SheetTitle>
						{isCreating ? "New Document" : document?.key || "Document"}
					</SheetTitle>
				</SheetHeader>

				<div className="flex-1 flex flex-col gap-4 overflow-hidden mt-4">
					{/* Key field (create mode only) */}
					{isCreating && (
						<div className="space-y-2">
							<Label htmlFor="doc-key">Key (optional)</Label>
							<Input
								id="doc-key"
								value={key}
								onChange={(e) => setKey(e.target.value)}
								placeholder="unique-document-key"
							/>
						</div>
					)}

					{/* Editor */}
					<div className="flex-1 overflow-auto border rounded-md">
						<TiptapEditor
							content={content}
							onChange={setContent}
							editable={isEditing}
						/>
					</div>

					{/* Actions */}
					<div className="flex justify-end gap-2 py-2">
						{isEditing ? (
							<>
								{!isCreating && (
									<Button
										variant="outline"
										onClick={() => {
											setContent(document?.content || "");
											setIsEditing(false);
										}}
									>
										<X className="h-4 w-4 mr-1" />
										Cancel
									</Button>
								)}
								<Button onClick={handleSave} disabled={isSaving}>
									<Save className="h-4 w-4 mr-1" />
									{isSaving ? "Saving..." : "Save"}
								</Button>
							</>
						) : (
							<Button variant="outline" onClick={() => setIsEditing(true)}>
								<Pencil className="h-4 w-4 mr-1" />
								Edit
							</Button>
						)}
					</div>
				</div>
			</SheetContent>
		</Sheet>
	);
}
