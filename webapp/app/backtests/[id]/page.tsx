"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { use, useMemo } from "react";
import useSWR from "swr";

import { DrawdownChart, EquityChart } from "@/components/equity-chart";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
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
import { num, pct, signClass } from "@/lib/backtest-format";
import type { BacktestDetail } from "@/lib/types";

const TRADES_LIMIT = 200;

export default function BacktestDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data, error } = useSWR<BacktestDetail>(
    `/api/backtests/${encodeURIComponent(id)}`,
    fetcher,
  );

  const tradeColumns = useMemo(() => {
    const first = data?.trades?.[0];
    return first ? Object.keys(first) : [];
  }, [data]);

  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Backtest not found</CardTitle>
          <CardDescription>{String(error.message ?? error)}</CardDescription>
        </CardHeader>
      </Card>
    );
  }
  if (!data) return <Skeleton className="h-96 w-full" />;

  const m = data.metrics;
  const configEntries = Object.entries(data.config).filter(
    ([, value]) => typeof value !== "object" || value === null,
  );
  const params_ = data.config["params"];

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link
          href="/backtests"
          className="mb-2 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> all backtests
        </Link>
        <h1 className="text-xl font-semibold tracking-tight">{data.name}</h1>
        <p className="text-sm text-muted-foreground">
          artifact <code className="font-mono">{data.id}</code>
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-6">
        <Metric label="Total return" value={pct(m.total_return)} tone={m.total_return} />
        <Metric
          label="Annualized"
          value={pct(m.annualized_return)}
          tone={m.annualized_return}
        />
        <Metric label="Volatility" value={pct(m.annualized_vol)} />
        <Metric label="Sharpe" value={num(m.sharpe)} />
        <Metric label="Max drawdown" value={pct(m.max_drawdown, 1)} tone={-1} />
        <Metric label="Days" value={String(m.n_days)} />
      </div>

      <Card className="py-3">
        <CardHeader className="px-4 pb-0">
          <CardTitle className="text-base">Growth of $1</CardTitle>
        </CardHeader>
        <CardContent className="px-3">
          {data.points.length > 0 ? (
            <EquityChart points={data.points} />
          ) : (
            <p className="py-12 text-center text-sm text-muted-foreground">
              No return series stored for this artifact.
            </p>
          )}
        </CardContent>
      </Card>

      {data.points.length > 0 ? (
        <Card className="py-3">
          <CardHeader className="px-4 pb-0">
            <CardTitle className="text-base">Drawdown (%)</CardTitle>
          </CardHeader>
          <CardContent className="px-3">
            <DrawdownChart points={data.points} />
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Run configuration</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2 text-sm">
            {configEntries.map(([key, value]) => (
              <div key={key} className="flex justify-between gap-4 border-b pb-1 last:border-0">
                <span className="text-muted-foreground">{key}</span>
                <span className="font-mono">{String(value ?? "—")}</span>
              </div>
            ))}
            {params_ && typeof params_ === "object" ? (
              <>
                <p className="pt-1 text-muted-foreground">params</p>
                <pre className="overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs">
                  {JSON.stringify(params_, null, 2)}
                </pre>
              </>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              Trades{" "}
              {data.trades ? (
                <Badge variant="secondary" className="ml-1">
                  {data.trades.length}
                </Badge>
              ) : null}
            </CardTitle>
            {data.trades && data.trades.length > TRADES_LIMIT ? (
              <CardDescription>showing first {TRADES_LIMIT}</CardDescription>
            ) : null}
          </CardHeader>
          <CardContent>
            {!data.trades || data.trades.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                This artifact recorded no per-trade log (portfolio strategies
                rebalance daily instead of trading discretely).
              </p>
            ) : (
              <div className="max-h-96 overflow-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      {tradeColumns.map((column) => (
                        <TableHead key={column}>{column}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.trades.slice(0, TRADES_LIMIT).map((trade, index) => (
                      <TableRow key={index}>
                        {tradeColumns.map((column) => (
                          <TableCell key={column} className="tabular-nums">
                            {formatCell(trade[column])}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: number | null;
}) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className={`text-xl tabular-nums ${signClass(tone ?? null)}`}>
          {value}
        </CardTitle>
      </CardHeader>
    </Card>
  );
}

function formatCell(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(4);
  }
  return String(value);
}
