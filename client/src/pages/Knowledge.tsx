/**
 * Knowledge Management Page
 *
 * Admin page for managing knowledge sources and their documents.
 * Knowledge sources are first-class entities with role-based access.
 */

import { useState, useEffect, useCallback } from "react";
import {
	Plus,
	RefreshCw,
	BookOpen,
	Pencil,
	Trash2,
	Globe,
	Building2,
	FileText,
	ChevronLeft,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
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
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { SearchBox } from "@/components/search/SearchBox";
import { useSearch } from "@/hooks/useSearch";
import { useAuth } from "@/contexts/AuthContext";
import { toast } from "sonner";
import { authFetch } from "@/lib/auth";
import { KnowledgeSourceDialog } from "@/components/knowledge/KnowledgeSourceDialog";
import { KnowledgeDocumentList } from "@/components/knowledge/KnowledgeDocumentList";

interface KnowledgeSource {
	id: string;
	name: string;
	namespace: string;
	description: string | null;
	organization_id: string | null;
	access_level: string;
	is_active: boolean;
	document_count: number;
	created_at: string;
}

export function Knowledge() {
	const { isPlatformAdmin } = useAuth();
	const [sources, setSources] = useState<KnowledgeSource[]>([]);
	const [isLoading, setIsLoading] = useState(true);
	const [searchTerm, setSearchTerm] = useState("");
	const [isDialogOpen, setIsDialogOpen] = useState(false);
	const [editSource, setEditSource] = useState<KnowledgeSource | null>(null);
	const [deleteSource, setDeleteSource] = useState<KnowledgeSource | null>(null);
	const [selectedSource, setSelectedSource] = useState<KnowledgeSource | null>(null);

	const fetchSources = useCallback(async () => {
		setIsLoading(true);
		try {
			const response = await authFetch("/api/knowledge-sources");
			if (response.ok) {
				const data = await response.json();
				setSources(data);
			}
		} catch (error) {
			toast.error("Failed to load knowledge sources");
		} finally {
			setIsLoading(false);
		}
	}, []);

	useEffect(() => {
		fetchSources();
	}, [fetchSources]);

	const filteredSources = useSearch(sources, searchTerm, [
		"name",
		"namespace",
		"description",
	]);

	const handleCreate = () => {
		setEditSource(null);
		setIsDialogOpen(true);
	};

	const handleEdit = (source: KnowledgeSource) => {
		setEditSource(source);
		setIsDialogOpen(true);
	};

	const handleDelete = async () => {
		if (!deleteSource) return;
		try {
			const response = await authFetch(`/api/knowledge-sources/${deleteSource.id}`, {
				method: "DELETE",
			});
			if (response.ok) {
				toast.success("Knowledge source deleted");
				fetchSources();
			}
		} catch {
			toast.error("Failed to delete knowledge source");
		}
		setDeleteSource(null);
	};

	// If a source is selected, show its documents
	if (selectedSource) {
		return (
			<div className="h-[calc(100vh-8rem)] flex flex-col space-y-6">
				<div className="flex items-center gap-3">
					<Button variant="ghost" size="sm" onClick={() => setSelectedSource(null)}>
						<ChevronLeft className="h-4 w-4 mr-1" />
						Back
					</Button>
					<div>
						<h1 className="text-2xl font-bold">{selectedSource.name}</h1>
						<p className="text-sm text-muted-foreground">
							Namespace: {selectedSource.namespace}
						</p>
					</div>
				</div>
				<KnowledgeDocumentList
					sourceId={selectedSource.id}
					namespace={selectedSource.namespace}
				/>
			</div>
		);
	}

	return (
		<div className="h-[calc(100vh-8rem)] flex flex-col space-y-6">
			{/* Header */}
			<div className="flex items-center justify-between">
				<div>
					<h1 className="text-4xl font-extrabold tracking-tight">Knowledge</h1>
					<p className="mt-2 text-muted-foreground">
						Manage knowledge sources and documents for AI agents
					</p>
				</div>
				<div className="flex gap-2">
					<Button variant="outline" size="icon" onClick={fetchSources} title="Refresh">
						<RefreshCw className="h-4 w-4" />
					</Button>
					<Button variant="outline" size="icon" onClick={handleCreate} title="Create Knowledge Source">
						<Plus className="h-4 w-4" />
					</Button>
				</div>
			</div>

			{/* Search */}
			<SearchBox
				value={searchTerm}
				onChange={setSearchTerm}
				placeholder="Search knowledge sources..."
				className="max-w-md"
			/>

			{/* Content */}
			{isLoading ? (
				<div className="space-y-2">
					{[...Array(3)].map((_, i) => (
						<Skeleton key={i} className="h-12 w-full" />
					))}
				</div>
			) : filteredSources.length > 0 ? (
				<DataTable>
					<DataTableHeader>
						<DataTableRow>
							<DataTableHead>Name</DataTableHead>
							<DataTableHead>Namespace</DataTableHead>
							<DataTableHead>Documents</DataTableHead>
							<DataTableHead>Access</DataTableHead>
							<DataTableHead>Scope</DataTableHead>
							<DataTableHead className="text-right" />
						</DataTableRow>
					</DataTableHeader>
					<DataTableBody>
						{filteredSources.map((source) => (
							<DataTableRow
								key={source.id}
								className="cursor-pointer"
								onClick={() => setSelectedSource(source)}
							>
								<DataTableCell className="font-medium">
									<div className="flex items-center gap-2">
										<BookOpen className="h-4 w-4 text-muted-foreground" />
										{source.name}
									</div>
								</DataTableCell>
								<DataTableCell className="text-muted-foreground font-mono text-xs">
									{source.namespace}
								</DataTableCell>
								<DataTableCell>
									<Badge variant="secondary" className="text-xs">
										<FileText className="mr-1 h-3 w-3" />
										{source.document_count}
									</Badge>
								</DataTableCell>
								<DataTableCell>
									<Badge variant="outline" className="text-xs capitalize">
										{source.access_level}
									</Badge>
								</DataTableCell>
								<DataTableCell>
									{source.organization_id ? (
										<Badge variant="outline" className="text-xs">
											<Building2 className="mr-1 h-3 w-3" />
											Org
										</Badge>
									) : (
										<Badge variant="default" className="text-xs">
											<Globe className="mr-1 h-3 w-3" />
											Global
										</Badge>
									)}
								</DataTableCell>
								<DataTableCell className="text-right">
									<div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
										<Button
											variant="ghost"
											size="icon-sm"
											onClick={() => handleEdit(source)}
										>
											<Pencil className="h-4 w-4" />
										</Button>
										<Button
											variant="ghost"
											size="icon-sm"
											onClick={() => setDeleteSource(source)}
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
						<BookOpen className="h-12 w-12 text-muted-foreground" />
						<h3 className="mt-4 text-lg font-semibold">No knowledge sources</h3>
						<p className="mt-2 text-sm text-muted-foreground">
							Create a knowledge source to start adding documents for AI agents
						</p>
						<Button variant="outline" onClick={handleCreate} className="mt-4">
							<Plus className="h-4 w-4 mr-2" />
							Create Knowledge Source
						</Button>
					</CardContent>
				</Card>
			)}

			{/* Delete Confirmation */}
			<AlertDialog open={!!deleteSource} onOpenChange={() => setDeleteSource(null)}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete Knowledge Source?</AlertDialogTitle>
						<AlertDialogDescription>
							This will deactivate "{deleteSource?.name}". Documents will be preserved.
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

			{/* Create/Edit Dialog */}
			<KnowledgeSourceDialog
				open={isDialogOpen}
				onClose={() => {
					setIsDialogOpen(false);
					setEditSource(null);
					fetchSources();
				}}
				source={editSource}
			/>
		</div>
	);
}

export default Knowledge;
