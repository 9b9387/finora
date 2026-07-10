import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiGet, ApiError } from "@/lib/api";
import { formatBytes, formatCount } from "@/lib/format";
import type { StoreOverview } from "@/lib/types";

export const dynamic = "force-dynamic";

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl tabular-nums">{value}</CardTitle>
        {hint ? (
          <CardDescription className="text-xs">{hint}</CardDescription>
        ) : null}
      </CardHeader>
    </Card>
  );
}

export default async function OverviewPage() {
  let overview: StoreOverview;
  try {
    overview = await apiGet<StoreOverview>("/api/store/overview");
  } catch (error) {
    const message =
      error instanceof ApiError ? error.message : "Failed to load the store overview.";
    return (
      <Card>
        <CardHeader>
          <CardTitle>Data API unavailable</CardTitle>
          <CardDescription>{message}</CardDescription>
          <CardDescription>
            Start it with <code className="font-mono">uv run finora serve</code>.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const staleCount = overview.symbols.filter((s) => !s.fresh).length;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Store overview</h1>
        <p className="text-sm text-muted-foreground">
          Local Parquet/DuckDB market store health at a glance.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Symbols" value={String(overview.symbol_count)} />
        <Stat label="Daily bars" value={formatCount(overview.total_rows)} />
        <Stat label="Store size" value={formatBytes(overview.store_size_bytes)} />
        <Stat
          label="Last completed session"
          value={overview.last_completed_session}
          hint={
            staleCount === 0
              ? "all symbols fresh"
              : `${staleCount} symbol${staleCount > 1 ? "s" : ""} stale — run finora etl`
          }
        />
      </div>

      <Card className="py-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Symbol</TableHead>
              <TableHead className="text-right">Bars</TableHead>
              <TableHead>First date</TableHead>
              <TableHead>Last date</TableHead>
              <TableHead>Freshness</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {overview.symbols.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="py-8 text-center text-muted-foreground">
                  The store is empty — run{" "}
                  <code className="font-mono">finora universe</code> then{" "}
                  <code className="font-mono">finora etl</code>.
                </TableCell>
              </TableRow>
            ) : (
              overview.symbols.map((s) => (
                <TableRow key={s.symbol}>
                  <TableCell>
                    <Link
                      href={`/symbols/${s.symbol}`}
                      className="font-medium underline-offset-4 hover:underline"
                    >
                      {s.symbol}
                    </Link>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCount(s.rows)}
                  </TableCell>
                  <TableCell className="tabular-nums">{s.first_date}</TableCell>
                  <TableCell className="tabular-nums">{s.last_date}</TableCell>
                  <TableCell>
                    {s.fresh ? (
                      <Badge className="bg-emerald-600/15 text-emerald-700 dark:text-emerald-400">
                        fresh
                      </Badge>
                    ) : (
                      <Badge className="bg-amber-500/15 text-amber-700 dark:text-amber-400">
                        stale
                      </Badge>
                    )}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
