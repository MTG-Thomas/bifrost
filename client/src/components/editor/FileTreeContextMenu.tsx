import {
	FilePlus,
	FolderPlus,
	Trash2,
	Edit2,
} from "lucide-react";
import type { FileMetadata } from "@/services/fileService";
import {
	ContextMenu,
	ContextMenuContent,
	ContextMenuItem,
	ContextMenuSeparator,
	ContextMenuTrigger,
} from "@/components/ui/context-menu";

export interface FileTreeContextMenuProps {
	file: FileMetadata;
	isFolder: boolean;
	onCreateFile: (folderPath?: string) => void;
	onCreateFolder: (folderPath?: string) => void;
	onRename: (file: FileMetadata) => void;
	onDelete: (file: FileMetadata) => void;
	children: React.ReactNode;
}

export function FileTreeContextMenu({
	file,
	isFolder,
	onCreateFile,
	onCreateFolder,
	onRename,
	onDelete,
	children,
}: FileTreeContextMenuProps) {
	return (
		<ContextMenu>
			<ContextMenuTrigger asChild>
				{children}
			</ContextMenuTrigger>
			<ContextMenuContent>
				{isFolder && (
					<>
						<ContextMenuItem
							onClick={() => onCreateFile(file.path)}
						>
							<FilePlus className="mr-2 h-4 w-4" />
							New File
						</ContextMenuItem>
						<ContextMenuItem
							onClick={() => onCreateFolder(file.path)}
						>
							<FolderPlus className="mr-2 h-4 w-4" />
							New Folder
						</ContextMenuItem>
						<ContextMenuSeparator />
					</>
				)}
				<ContextMenuItem onClick={() => onRename(file)}>
					<Edit2 className="mr-2 h-4 w-4" />
					Rename
				</ContextMenuItem>
				<ContextMenuSeparator />
				<ContextMenuItem
					onClick={() => onDelete(file)}
					className="text-destructive focus:text-destructive"
				>
					<Trash2 className="mr-2 h-4 w-4" />
					Delete
				</ContextMenuItem>
			</ContextMenuContent>
		</ContextMenu>
	);
}
