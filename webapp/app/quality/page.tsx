"use client";

import { CheckCircle2, RefreshCw } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
import type { QualityResponse, SymbolList } from "@/lib/types";

const KIND_STYLES: Record<string, string> = {
  missing_days: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  extreme_return: "bg-rose-500/15 text-rose-700 dark:text-rose-400",
  nonpositive_volume: "bg-violet-500/15 text-violet-700 dark:text-violet-400",
  low_price: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
};

const ALL = "__all__";

export default function QualityPage() {
  const [scope, setScope] = useState<string>(ALL);
  const { data: symbolsData } = useSWR<SymbolList>("/api/symbols", fetcher);

  const key = scope === ALL ? "/api/quality" : `/api/quality?symbol=${scope}`;
  const { data, error, isValidating, mutate } = useSWR<QualityResponse>(key, fetcher, {
    revalidateOnFocus: false,
  });

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex-1">
          <h1 className="text-xl font-semibold tracking-tight">Data quality</h1>
          <p className="text-sm text-muted-foreground">
            Checks recomputed on demand over the local store with the thresholds
            from <code className="font-mono">config/data.yaml</code>.
          </p>
        </div>
        <Select value={scope} onValueChange={setScope}>
          <SelectTrigger className="w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All symbols</SelectItem>
            {(symbolsData?.symbols ?? []).map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant="outline"
          onClick={() => mutate()}
          disabled={isValidating}
          className="gap-2"
        >
          <RefreshCw className={`h-4 w-4 ${isValidating ? "animate-spin" : ""}`} />
          Re-run checks
        </Button>
      </div>

      {data ? (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <ThresholdCard
            label="Max missing run"
            value={`${data.thresholds.max_missing_run_days} days`}
            hint="consecutive NYSE trading days"
          />
          <ThresholdCard
            label="Max |daily return|"
            value={`${(data.thresholds.max_abs_daily_return * 100).toFixed(0)}%`}
            hint="beyond this flags a bad print"
          />
          <ThresholdCard
            label="Min price"
            value={`$${data.thresholds.min_price.toFixed(2)}`}
            hint="closes below are flagged"
          />
          <ThresholdCard
            label="Checked"
            value={`${data.checked_symbols} symbols`}
            hint={`at ${new Date(data.generated_at).toLocaleTimeString()}`}
          />
        </div>
      ) : null}

      {error ? (
        <Card>
          <CardHeader>
            <CardTitle>Data API unavailable</CardTitle>
            <CardDescription>{String(error.message ?? error)}</CardDescription>
          </CardHeader>
        </Card>
      ) : !data ? (
        <Skeleton className="h-64 w-full" />
      ) : data.issues.length === 0 ? (
        <Card>
          <CardContent className="flex items-center gap-3 py-10">
            <CheckCircle2 className="h-6 w-6 text-emerald-500" />
            <div>
              <p className="font-medium">No quality issues</p>
              <p className="text-sm text-muted-foreground">
                Every checked series passed the gap, volume, return, and price checks.
              </p>
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card className="py-0">
          <CardHeader className="pt-4">
            <CardTitle className="text-base">
              {data.issues.length} issue{data.issues.length > 1 ? "s" : ""}
            </CardTitle>
            <CardDescription>
              Flags, not failures — the ETL never drops flagged rows. Sub-$1
              closes on heavily split names (e.g. old NVDA) are expected.
            </CardDescription>
          </CardHeader>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Symbol</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead>Date</TableHead>
                  <TableHead>Detail</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.issues.map((issue, index) => (
                  <TableRow key={index}>
                    <TableCell className="font-medium">{issue.symbol}</TableCell>
                    <TableCell>
                      <Badge
                        className={
                          KIND_STYLES[issue.kind] ?? "bg-muted text-muted-foreground"
                        }
                      >
                        {issue.kind}
                      </Badge>
                    </TableCell>
                    <TableCell className="tabular-nums">{issue.date ?? "—"}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {issue.detail}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </Card>
      )}
    </div>
  );
}

function ThresholdCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-xl tabular-nums">{value}</CardTitle>
        <CardDescription className="text-xs">{hint}</CardDescription>
      </CardHeader>
    </Card>
  );
}
