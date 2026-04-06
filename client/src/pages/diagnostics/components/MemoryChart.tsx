// client/src/pages/diagnostics/components/MemoryChart.tsx
import { useMemo, useState } from "react";
import {
    AreaChart,
    Area,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    ResponsiveContainer,
    ReferenceLine,
} from "recharts";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useWorkerMetrics, type WorkerMetricPoint } from "@/services/workers";

const TIME_RANGES = ["1h", "6h", "24h", "7d"] as const;
type TimeRange = (typeof TIME_RANGES)[number];

// Consistent colors for up to 10 containers
const CONTAINER_COLORS = [
    "hsl(var(--chart-1))",
    "hsl(var(--chart-2))",
    "hsl(var(--chart-3))",
    "hsl(var(--chart-4))",
    "hsl(var(--chart-5))",
    "#f97316",
    "#06b6d4",
    "#8b5cf6",
    "#ec4899",
    "#14b8a6",
];

interface ChartDataPoint {
    timestamp: string;
    label: string;
    [workerId: string]: number | string;
}

function formatBytes(bytes: number): string {
    if (bytes < 0) return "N/A";
    const gb = bytes / (1024 * 1024 * 1024);
    if (gb >= 1) return `${gb.toFixed(1)} GB`;
    const mb = bytes / (1024 * 1024);
    return `${mb.toFixed(0)} MB`;
}

function formatTimeLabel(isoString: string, range: TimeRange): string {
    const date = new Date(isoString);
    if (range === "1h" || range === "6h") {
        return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    if (range === "24h") {
        return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    return date.toLocaleDateString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

interface MemoryChartProps {
    /** Optional live data points from WebSocket to append */
    livePoints?: WorkerMetricPoint[];
}

export function MemoryChart({ livePoints }: MemoryChartProps) {
    const [range, setRange] = useState<TimeRange>("1h");
    const { data, isLoading } = useWorkerMetrics(range);

    const { chartData, workerIds, totalCurrent, totalMax, hasUnlimitedWorker } = useMemo(() => {
        const allPoints = [...(data?.points ?? []), ...(livePoints ?? [])];
        if (allPoints.length === 0) {
            return {
                chartData: [],
                workerIds: [],
                totalCurrent: 0,
                totalMax: 0,
                hasUnlimitedWorker: false,
            };
        }

        // Get unique worker IDs
        const ids = [...new Set(allPoints.map((p) => p.worker_id))];

        // Group by timestamp
        const byTimestamp = new Map<string, Map<string, WorkerMetricPoint>>();
        for (const point of allPoints) {
            if (!byTimestamp.has(point.timestamp)) {
                byTimestamp.set(point.timestamp, new Map());
            }
            byTimestamp.get(point.timestamp)!.set(point.worker_id, point);
        }

        // Build chart data
        const result: ChartDataPoint[] = [];
        const sortedTimestamps = [...byTimestamp.keys()].sort();
        for (const ts of sortedTimestamps) {
            const workers = byTimestamp.get(ts)!;
            const row: ChartDataPoint = {
                timestamp: ts,
                label: formatTimeLabel(ts, range),
            };
            for (const id of ids) {
                const point = workers.get(id);
                row[id] = point ? point.memory_current : 0;
            }
            result.push(row);
        }

        // Compute current totals from latest data points
        const latestByWorker = new Map<string, WorkerMetricPoint>();
        for (const point of allPoints) {
            const existing = latestByWorker.get(point.worker_id);
            if (!existing || point.timestamp > existing.timestamp) {
                latestByWorker.set(point.worker_id, point);
            }
        }
        let current = 0;
        let max = 0;
        let unlimited = false;
        for (const point of latestByWorker.values()) {
            current += Math.max(0, point.memory_current);
            if (point.memory_max > 0) {
                max += point.memory_max;
            } else {
                unlimited = true;
            }
        }

        return {
            chartData: result,
            workerIds: ids,
            totalCurrent: current,
            totalMax: max,
            hasUnlimitedWorker: unlimited,
        };
    }, [data, livePoints, range]);

    const hasData = chartData.length > 0;
    const showLimit = totalMax > 0 && !hasUnlimitedWorker;
    const thresholdBytes = totalMax * 0.85;
    const utilizationPct = showLimit ? ((totalCurrent / totalMax) * 100).toFixed(0) : "0";

    if (isLoading) {
        return (
            <Card>
                <CardContent className="pt-6">
                    <Skeleton className="h-[250px] w-full" />
                </CardContent>
            </Card>
        );
    }

    return (
        <Card>
            <CardContent className="pt-6">
                {/* Header */}
                <div className="flex items-start justify-between mb-4">
                    <div>
                        <div className="text-xs text-muted-foreground uppercase tracking-wider">
                            Total Memory Usage
                        </div>
                        <div className="flex items-baseline gap-2 mt-1">
                            {!hasData ? (
                                <span className="text-sm text-muted-foreground">
                                    No metrics data yet
                                </span>
                            ) : showLimit ? (
                                <>
                                    <span className="text-3xl font-bold">
                                        {formatBytes(totalCurrent)}
                                    </span>
                                    <span className="text-sm text-muted-foreground">
                                        / {formatBytes(totalMax)} across{" "}
                                        {workerIds.length} container
                                        {workerIds.length !== 1 ? "s" : ""}
                                    </span>
                                </>
                            ) : (
                                <>
                                    <span className="text-3xl font-bold">
                                        {formatBytes(totalCurrent)}
                                    </span>
                                    <span className="text-sm text-muted-foreground">
                                        across {workerIds.length} container
                                        {workerIds.length !== 1 ? "s" : ""}{" "}
                                        &middot; no memory limit set
                                    </span>
                                </>
                            )}
                        </div>
                        {showLimit && (
                            <div className="text-xs text-muted-foreground mt-0.5">
                                {utilizationPct}% utilized &middot; Threshold: 85%
                            </div>
                        )}
                    </div>
                    <div className="flex gap-1">
                        {TIME_RANGES.map((r) => (
                            <Button
                                key={r}
                                variant={range === r ? "default" : "ghost"}
                                size="sm"
                                className="h-7 px-3 text-xs"
                                onClick={() => setRange(r)}
                            >
                                {r}
                            </Button>
                        ))}
                    </div>
                </div>

                {/* Chart */}
                {chartData.length === 0 ? (
                    <div className="flex items-center justify-center h-[200px] text-muted-foreground text-sm">
                        No metrics data available yet
                    </div>
                ) : (
                    <ResponsiveContainer width="100%" height={200}>
                        <AreaChart data={chartData}>
                            <CartesianGrid
                                strokeDasharray="3 3"
                                className="stroke-muted"
                            />
                            <XAxis
                                dataKey="label"
                                tick={{ fontSize: 11 }}
                                tickLine={false}
                                axisLine={false}
                            />
                            <YAxis
                                tick={{ fontSize: 11 }}
                                tickLine={false}
                                axisLine={false}
                                tickFormatter={(v) => formatBytes(v)}
                                width={60}
                            />
                            <Tooltip
                                contentStyle={{
                                    backgroundColor: "hsl(var(--card))",
                                    border: "1px solid hsl(var(--border))",
                                    borderRadius: "6px",
                                    fontSize: "12px",
                                }}
                                formatter={(value: number, name: string) => [
                                    formatBytes(value),
                                    name,
                                ]}
                                labelFormatter={(label) => label}
                            />
                            {showLimit && (
                                <ReferenceLine
                                    y={thresholdBytes}
                                    stroke="hsl(var(--destructive))"
                                    strokeDasharray="4 4"
                                    strokeOpacity={0.5}
                                    label={{
                                        value: "85%",
                                        position: "right",
                                        style: {
                                            fontSize: 10,
                                            fill: "hsl(var(--destructive))",
                                        },
                                    }}
                                />
                            )}
                            {workerIds.map((id, i) => (
                                <Area
                                    key={id}
                                    type="monotone"
                                    dataKey={id}
                                    stackId="memory"
                                    fill={CONTAINER_COLORS[i % CONTAINER_COLORS.length]}
                                    fillOpacity={0.2}
                                    stroke={CONTAINER_COLORS[i % CONTAINER_COLORS.length]}
                                    strokeWidth={1.5}
                                />
                            ))}
                        </AreaChart>
                    </ResponsiveContainer>
                )}

                {/* Legend */}
                {workerIds.length > 0 && (
                    <div className="flex flex-wrap gap-4 mt-3 text-xs">
                        {workerIds.map((id, i) => (
                            <span
                                key={id}
                                className="flex items-center gap-1.5"
                            >
                                <span
                                    className="inline-block w-2.5 h-0.5 rounded-sm"
                                    style={{
                                        backgroundColor:
                                            CONTAINER_COLORS[
                                                i % CONTAINER_COLORS.length
                                            ],
                                    }}
                                />
                                {id}
                            </span>
                        ))}
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
