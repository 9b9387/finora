"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";

import { Badge } from "@/components/ui/badge";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { fetcher } from "@/lib/api";
import type { SnapshotDetail, SnapshotList, UniverseDiff } from "@/lib/types";

export default function UniversePage() {
  const { data: list, error } = useSWR<SnapshotList>(
    "/api/universe/snapshots",
    fetcher,
  );
  const snapshots = useMemo(() => list?.snapshots ?? [], [list]);

  const [selected, setSelected] = useState<string | undefined>();
  const [diffFrom, setDiffFrom] = useState<string | undefined>();
  const [diffTo, setDiffTo] = useState<string | undefined>();
  const [filter, setFilter] = useState("");

  const activeDate = selected ?? snapshots[0]?.date;
  // default diff: previous snapshot -> newest
  const activeFrom = diffFrom ?? snapshots[1]?.date;
  const activeTo = diffTo ?? snapshots[0]?.date;

  const { data: detail } = useSWR<SnapshotDetail>(
    activeDate ? `/api/universe/snapshots/${activeDate}` : null,
    fetcher,
  );
  const { data: diff } = useSWR<UniverseDiff>(
    activeFrom && activeTo && activeFrom !== activeTo
      ? `/api/universe/diff?from=${activeFrom}&to=${activeTo}`
      : null,
    fetcher,
  );

  const filtered = useMemo(() => {
    const symbols = detail?.symbols ?? [];
    const query = filter.trim().toUpperCase();
    return query ? symbols.filter((s) => s.includes(query)) : symbols;
  }, [detail, filter]);

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
  if (!list) {
    return <Skeleton className="h-64 w-full" />;
  }
  if (snapshots.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>No universe snapshots yet</CardTitle>
          <CardDescription>
            Run <code className="font-mono">uv run finora universe</code> to snapshot
            the current S&amp;P 500 constituents; each run stores a dated CSV under{" "}
            <code className="font-mono">data/universe/</code> to preserve
            point-in-time membership history.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Universe snapshots</h1>
        <p className="text-sm text-muted-foreground">
          Point-in-time S&amp;P 500 membership, one dated CSV per snapshot.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Constituents</CardTitle>
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <Select value={activeDate} onValueChange={setSelected}>
                <SelectTrigger className="w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {snapshots.map((s) => (
                    <SelectItem key={s.date} value={s.date}>
                      {s.date} ({s.symbol_count})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <input
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                placeholder="Filter symbols…"
                className="h-9 flex-1 rounded-md border bg-transparent px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </div>
            <CardDescription>
              {detail
                ? `${filtered.length} of ${detail.symbols.length} symbols`
                : "loading…"}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {!detail ? (
              <Skeleton className="h-48 w-full" />
            ) : (
              <div className="max-h-96 overflow-y-auto rounded-md border p-3">
                <div className="flex flex-wrap gap-1.5">
                  {filtered.map((symbol) => (
                    <Badge key={symbol} variant="secondary" className="font-mono">
                      {symbol}
                    </Badge>
                  ))}
                  {filtered.length === 0 ? (
                    <span className="text-sm text-muted-foreground">no matches</span>
                  ) : null}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Diff two snapshots</CardTitle>
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <Select value={activeFrom} onValueChange={setDiffFrom}>
                <SelectTrigger className="w-40">
                  <SelectValue placeholder="from" />
                </SelectTrigger>
                <SelectContent>
                  {snapshots.map((s) => (
                    <SelectItem key={s.date} value={s.date}>
                      {s.date}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <span className="text-sm text-muted-foreground">→</span>
              <Select value={activeTo} onValueChange={setDiffTo}>
                <SelectTrigger className="w-40">
                  <SelectValue placeholder="to" />
                </SelectTrigger>
                <SelectContent>
                  {snapshots.map((s) => (
                    <SelectItem key={s.date} value={s.date}>
                      {s.date}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <CardDescription>
              {snapshots.length < 2
                ? "Need at least two snapshots to diff."
                : diff
                  ? `+${diff.added.length} added · −${diff.removed.length} removed · ${diff.unchanged_count} unchanged`
                  : activeFrom === activeTo
                    ? "Pick two different snapshots."
                    : "loading…"}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {diff ? (
              <Tabs defaultValue="added">
                <TabsList>
                  <TabsTrigger value="added">Added ({diff.added.length})</TabsTrigger>
                  <TabsTrigger value="removed">
                    Removed ({diff.removed.length})
                  </TabsTrigger>
                </TabsList>
                <TabsContent value="added">
                  <SymbolCloud symbols={diff.added} tone="added" />
                </TabsContent>
                <TabsContent value="removed">
                  <SymbolCloud symbols={diff.removed} tone="removed" />
                </TabsContent>
              </Tabs>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function SymbolCloud({
  symbols,
  tone,
}: {
  symbols: string[];
  tone: "added" | "removed";
}) {
  if (symbols.length === 0) {
    return <p className="py-4 text-sm text-muted-foreground">none</p>;
  }
  const className =
    tone === "added"
      ? "bg-emerald-600/15 text-emerald-700 dark:text-emerald-400"
      : "bg-rose-500/15 text-rose-700 dark:text-rose-400";
  return (
    <div className="flex max-h-72 flex-wrap gap-1.5 overflow-y-auto pt-2">
      {symbols.map((symbol) => (
        <Badge key={symbol} className={`font-mono ${className}`}>
          {symbol}
        </Badge>
      ))}
    </div>
  );
}
