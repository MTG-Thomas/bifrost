/**
 * Knowledge Document List
 *
 * Lists and manages documents within a knowledge source.
 */

import { useState, useEffect, useCallback } from "react";
import { Plus, FileText, Trash2, Eye } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent } from "@/components/ui/card";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { toast } from "sonner";
import { authFetch } from "@/lib/api-client";
import { KnowledgeDocumentDrawer } from "@/components/knowledge/KnowledgeDocumentDrawer";

interface DocumentSummary {
	id: string;
	namespace: string;
	key: string | null;
	content_preview: string;
	metadata: Record<string, unknown>;
	created_at: string | null;
}

interface KnowledgeDocumentListProps {
	sourceId: string;
	namespace: string;
}

export function KnowledgeDocumentList({ sourceId, namespace: _namespace }: KnowledgeDocumentListProps) {
	const [documents, setDocuments] = useState<DocumentSummary[]>([]);
	const [isLoading, setIsLoading] = useState(true);
	const [deleteDoc, setDeleteDoc] = useState<DocumentSummary | null>(null);
	const [viewDocId, setViewDocId] = useState<string | null>(null);
	const [isCreating, setIsCreating] = useState(false);

	const fetchDocuments = useCallback(async () => {
		setIsLoading(true);
		try {
			const response = await authFetch(`/api/knowledge-sources/${sourceId}/documents`);
			if (response.ok) {
				const data = await response.json();
				setDocuments(data);
			}
		} catch {
			toast.error("Failed to load documents");
		} finally {
			setIsLoading(false);
		}
	}, [sourceId]);

	useEffect(() => {
		fetchDocuments();
	}, [fetchDocuments]);

	const handleDelete = async () => {
		if (!deleteDoc) return;
		try {
			const response = await authFetch(
				`/api/knowledge-sources/${sourceId}/documents/${deleteDoc.id}`,
				{ method: "DELETE" },
			);
			if (response.ok) {
				toast.success("Document deleted");
				fetchDocuments();
			}
		} catch {
			toast.error("Failed to delete document");
		}
		setDeleteDoc(null);
	};

	return (
		<div className="flex flex-col space-y-4 flex-1">
			<div className="flex items-center justify-between">
				<Badge variant="secondary">
					<FileText className="mr-1 h-3 w-3" />
					{documents.length} documents
				</Badge>
				<Button variant="outline" size="sm" onClick={() => setIsCreating(true)}>
					<Plus className="h-4 w-4 mr-1" />
					Add Document
				</Button>
			</div>

			{isLoading ? (
				<div className="space-y-2">
					{[...Array(3)].map((_, i) => (
						<Skeleton key={i} className="h-12 w-full" />
					))}
				</div>
			) : documents.length > 0 ? (
				<DataTable>
					<DataTableHeader>
						<DataTableRow>
							<DataTableHead>Key</DataTableHead>
							<DataTableHead>Preview</DataTableHead>
							<DataTableHead>Created</DataTableHead>
							<DataTableHead className="text-right" />
						</DataTableRow>
					</DataTableHeader>
					<DataTableBody>
						{documents.map((doc) => (
							<DataTableRow key={doc.id}>
								<DataTableCell className="font-mono text-xs">
									{doc.key || "-"}
								</DataTableCell>
								<DataTableCell className="max-w-md truncate text-muted-foreground text-sm">
									{doc.content_preview || "No content"}
								</DataTableCell>
								<DataTableCell className="text-xs text-muted-foreground">
									{doc.created_at
										? new Date(doc.created_at).toLocaleDateString()
										: "-"}
								</DataTableCell>
								<DataTableCell className="text-right">
									<div className="flex justify-end gap-1">
										<Button
											variant="ghost"
											size="icon-sm"
											onClick={() => setViewDocId(doc.id)}
										>
											<Eye className="h-4 w-4" />
										</Button>
										<Button
											variant="ghost"
											size="icon-sm"
											onClick={() => setDeleteDoc(doc)}
										>
											<Trash2 className="h-4 w-4" />
										</Button>
									</div>
								</DataTableCell>
							</DataTableRow>
						))}
					</DataTableBody>
				</DataTable>
			) : (
				<Card>
					<CardContent className="flex flex-col items-center justify-center py-12 text-center">
						<FileText className="h-12 w-12 text-muted-foreground" />
						<h3 className="mt-4 text-lg font-semibold">No documents</h3>
						<p className="mt-2 text-sm text-muted-foreground">
							Add documents to this knowledge source for AI agent RAG
						</p>
						<Button variant="outline" onClick={() => setIsCreating(true)} className="mt-4">
							<Plus className="h-4 w-4 mr-2" />
							Add Document
						</Button>
					</CardContent>
				</Card>
			)}

			{/* Delete Confirmation */}
			<AlertDialog open={!!deleteDoc} onOpenChange={() => setDeleteDoc(null)}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete Document?</AlertDialogTitle>
						<AlertDialogDescription>
							This will permanently delete this document and its embeddings.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							Delete
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			{/* View/Edit Document Drawer */}
			<KnowledgeDocumentDrawer
				sourceId={sourceId}
				documentId={viewDocId}
				isCreating={isCreating}
				onClose={() => {
					setViewDocId(null);
					setIsCreating(false);
					fetchDocuments();
				}}
			/>
		</div>
	);
}
