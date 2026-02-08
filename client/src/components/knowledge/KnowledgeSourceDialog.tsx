/**
 * Knowledge Source Dialog
 *
 * Dialog for creating and editing knowledge sources.
 */

import { useEffect, useState } from "react";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";
import { authFetch } from "@/lib/api-client";

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

interface KnowledgeSourceDialogProps {
	open: boolean;
	onClose: () => void;
	source?: KnowledgeSource | null;
}

export function KnowledgeSourceDialog({ open, onClose, source }: KnowledgeSourceDialogProps) {
	const isEditing = !!source;
	const [name, setName] = useState("");
	const [description, setDescription] = useState("");
	const [accessLevel, setAccessLevel] = useState("role_based");
	const [isSaving, setIsSaving] = useState(false);

	useEffect(() => {
		if (source) {
			setName(source.name);
			setDescription(source.description || "");
			setAccessLevel(source.access_level);
		} else {
			setName("");
			setDescription("");
			setAccessLevel("role_based");
		}
	}, [source, open]);

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!name.trim()) return;

		setIsSaving(true);
		try {
			const url = isEditing
				? `/api/knowledge-sources/${source.id}`
				: "/api/knowledge-sources";
			const method = isEditing ? "PUT" : "POST";

			const body: Record<string, unknown> = {
				name: name.trim(),
				description: description.trim() || null,
				access_level: accessLevel,
			};

			const response = await authFetch(url, {
				method,
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(body),
			});

			if (response.ok) {
				toast.success(isEditing ? "Knowledge source updated" : "Knowledge source created");
				onClose();
			} else {
				const err = await response.json().catch(() => ({}));
				toast.error(err.detail || "Failed to save knowledge source");
			}
		} catch {
			toast.error("Failed to save knowledge source");
		} finally {
			setIsSaving(false);
		}
	};

	return (
		<Dialog open={open} onOpenChange={() => onClose()}>
			<DialogContent className="sm:max-w-[500px]">
				<DialogHeader>
					<DialogTitle>
						{isEditing ? "Edit Knowledge Source" : "Create Knowledge Source"}
					</DialogTitle>
					<DialogDescription>
						{isEditing
							? "Update knowledge source settings"
							: "Create a new knowledge source for AI agent RAG"}
					</DialogDescription>
				</DialogHeader>

				<form onSubmit={handleSubmit} className="space-y-4">
					<div className="space-y-2">
						<Label htmlFor="name">Name</Label>
						<Input
							id="name"
							value={name}
							onChange={(e) => setName(e.target.value)}
							placeholder="Company Policies"
							required
						/>
					</div>

					<div className="space-y-2">
						<Label htmlFor="description">Description</Label>
						<Textarea
							id="description"
							value={description}
							onChange={(e) => setDescription(e.target.value)}
							placeholder="What knowledge does this source contain?"
						/>
					</div>

					<div className="space-y-2">
						<Label>Access Level</Label>
						<Select value={accessLevel} onValueChange={setAccessLevel}>
							<SelectTrigger>
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value="authenticated">Authenticated (all users)</SelectItem>
								<SelectItem value="role_based">Role Based</SelectItem>
							</SelectContent>
						</Select>
					</div>

					<DialogFooter>
						<Button type="button" variant="outline" onClick={onClose}>
							Cancel
						</Button>
						<Button type="submit" disabled={isSaving}>
							{isSaving ? "Saving..." : isEditing ? "Update" : "Create"}
						</Button>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
