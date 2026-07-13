"use client";

import Link from "next/link";
import useSWR from "swr";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fetcher } from "@/lib/api";
import { num, pct, signClass, stampToDate } from "@/lib/backtest-format";
import type { BacktestList } from "@/lib/types";

const KIND_STYLES: Record<string, string> = {
  qlib: "bg-violet-500/15 text-violet-700 dark:text-violet-400",
  momentum: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  rsi: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
};

export default function BacktestsPage() {
  const { data, error } = useSWR<BacktestList>("/api/backtests", fetcher);

  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Data API unavailable</CardTitle>
          <CardDescription>{String(error.message ?? error)}</CardDescription>
        </CardHeader>
      </Card>
    );
  }
  if (!data) return <Skeleton className="h-64 w-full" />;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Backtests</h1>
        <p className="text-sm text-muted-foreground">
          Artifacts under <code className="font-mono">artifacts/backtests/</code> —
          the evidence a strategy shows before promotion.
        </p>
      </div>

      {data.runs.length === 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>No backtest artifacts yet</CardTitle>
            <CardDescription>
              Run <code className="font-mono">uv run finora backtest</code> to create
              one per configured strategy.
            </CardDescription>
          </CardHeader>
        </Card>
      ) : (
        <Card className="py-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Strategy</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead>As of</TableHead>
                <TableHead className="text-right">Days</TableHead>
                <TableHead className="text-right">Total return</TableHead>
                <TableHead className="text-right">Annualized</TableHead>
                <TableHead className="text-right">Sharpe</TableHead>
                <TableHead className="text-right">Max DD</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.runs.map((run) => (
                <TableRow key={run.id}>
                  <TableCell>
                    <Link
                      href={`/backtests/${run.id}`}
                      className="font-medium underline-offset-4 hover:underline"
                    >
                      {run.name}
                    </Link>
                  </TableCell>
                  <TableCell>
                    {run.kind ? (
                      <Badge
                        className={
                          KIND_STYLES[run.kind] ?? "bg-muted text-muted-foreground"
                        }
                      >
                        {run.kind}
                      </Badge>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell className="tabular-nums">
                    {stampToDate(run.stamp)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {run.metrics.n_days}
                  </TableCell>
                  <TableCell
                    className={`text-right font-medium tabular-nums ${signClass(run.metrics.total_return)}`}
                  >
                    {pct(run.metrics.total_return)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {pct(run.metrics.annualized_return)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {num(run.metrics.sharpe)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-rose-600 dark:text-rose-400">
                    {pct(run.metrics.max_drawdown, 1)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </div>
  );
}
