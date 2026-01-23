/**
 * Create App Modal
 *
 * Wrapper around AppInfoDialog for creating new applications.
 */

import { useNavigate } from "react-router-dom";
import { AppInfoDialog } from "./AppInfoDialog";

interface CreateAppModalProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
}

export function CreateAppModal({ open, onOpenChange }: CreateAppModalProps) {
	const navigate = useNavigate();

	const handleCreated = (slug: string) => {
		navigate(`/apps/${slug}/edit`);
	};

	return (
		<AppInfoDialog
			open={open}
			onOpenChange={onOpenChange}
			onCreated={handleCreated}
		/>
	);
}
