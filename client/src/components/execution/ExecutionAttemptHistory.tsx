import { Badge } from "@/components/ui/badge";
import { formatDistanceToNow } from "date-fns";
import { parseBackendDate } from "@/lib/utils";
import type { components } from "@/lib/v1";

export type ExecutionAttemptView = components["schemas"]["ExecutionAttemptPublic"];
export type ExecutionAttemptHistoryView = Omit<
    components["schemas"]["ExecutionAttemptHistory"],
    "attempts"
> & { attempts: ExecutionAttemptView[] };

function readable(value: string) {
	return value.replaceAll("_", " ");
}

export function ExecutionAttemptHistory({
	history,
}: {
	history?: ExecutionAttemptHistoryView;
}) {
	if (!history) return null;
	if (history.coverage === "legacy_unavailable") {
		return (
			<section aria-labelledby="execution-attempts-heading">
				<h4
					id="execution-attempts-heading"
					className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
				>
					Execution attempts
				</h4>
				<p className="text-sm text-muted-foreground">
					Attempt history is unavailable for this legacy execution.
				</p>
			</section>
		);
	}
	if (history.attempts.length === 0) {
		return (
			<section aria-labelledby="execution-attempts-heading">
				<h4
					id="execution-attempts-heading"
					className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
				>
					Execution attempts
				</h4>
				<p className="text-sm text-muted-foreground">
					No worker attempt was claimed.
				</p>
			</section>
		);
	}

	return (
		<section aria-labelledby="execution-attempts-heading">
			<h4
				id="execution-attempts-heading"
				className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
			>
				Execution attempts
			</h4>
			<div className="space-y-2">
				{history.attempts.map((attempt) => (
					<div
						key={attempt.attempt_id}
						className="rounded-md border bg-muted/20 px-3 py-2 text-sm"
					>
						<div className="flex flex-wrap items-center gap-2">
							<span className="font-medium">
								Attempt {attempt.attempt_number}
							</span>
							<Badge variant="outline" className="capitalize">
								{readable(attempt.status)}
							</Badge>
							<span className="text-xs capitalize text-muted-foreground">
								{readable(attempt.failure_phase ?? attempt.phase)}
							</span>
							{attempt.duration_ms != null && (
								<span className="ml-auto text-xs tabular-nums text-muted-foreground">
									{attempt.duration_ms.toLocaleString()} ms
								</span>
							)}
						</div>
						<div className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted-foreground">
							<span>
								{attempt.claimed_at
									? "Claimed"
									: attempt.published_at
										? "Published"
										: "Created"}{" "}
								{formatDistanceToNow(parseBackendDate(
									attempt.claimed_at ??
										attempt.published_at ??
										attempt.created_at,
								), {
									addSuffix: true,
								})}
							</span>
							{attempt.failure_code && (
								<span className="font-mono">
									{attempt.failure_code}
								</span>
							)}
							{attempt.worker_id && <span>{attempt.worker_id}</span>}
						</div>
					</div>
				))}
			</div>
		</section>
	);
}
