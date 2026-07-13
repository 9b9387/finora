"use client";

import { Pencil, Play, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";

import { RunBacktestDialog } from "@/components/run-backtest-dialog";
import { StrategyFormDialog } from "@/components/strategy-form-dialog";
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { apiSend, fetcher } from "@/lib/api";
import { KIND_LABELS } from "@/lib/strategy-fields";
import type { StrategyListResponse, StrategyModel, SymbolList } from "@/lib/types";

const KIND_STYLES: Record<string, string> = {
  qlib: "bg-violet-500/15 text-violet-700 dark:text-violet-400",
  momentum: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  rsi: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  ma_cross: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
  bollinger: "bg-cyan-500/15 text-cyan-700 dark:text-cyan-400",
};

const STAGE_STYLES: Record<string, string> = {
  paper: "bg-muted text-muted-foreground",
  small: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  full: "bg-emerald-600/15 text-emerald-700 dark:text-emerald-400",
};

export function isEditableParam(value: unknown): boolean {
  return typeof value !== "object" || value === null;
}

export default function StrategiesPage() {
  const { data, error, mutate } = useSWR<StrategyListResponse>(
    "/api/strategies",
    fetcher,
  );
  const { data: symbolsData } = useSWR<SymbolList>("/api/symbols", fetcher);
  const symbols = symbolsData?.symbols ?? [];

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<StrategyModel | null>(null);
  const [runFor, setRunFor] = useState<StrategyModel | null>(null);
  const [deleting, setDeleting] = useState<StrategyModel | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const confirmDelete = async () => {
    if (!deleting) return;
    setDeleteError(null);
    try {
      await apiSend("DELETE", `/api/strategies/${encodeURIComponent(deleting.name)}`);
      setDeleting(null);
      mutate();
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : String(err));
    }
  };

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

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex-1">
          <h1 className="text-xl font-semibold tracking-tight">Strategies</h1>
          <p className="text-sm text-muted-foreground">
            Managed in <code className="font-mono">config/strategies.yaml</code>;
            promotion beyond paper stage stays a deliberate decision.
          </p>
        </div>
        <Button
          className="gap-2"
          onClick={() => {
            setEditing(null);
            setFormOpen(true);
          }}
        >
          <Plus className="h-4 w-4" /> New strategy
        </Button>
      </div>

      {!data ? (
        <Skeleton className="h-64 w-full" />
      ) : data.strategies.length === 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>No strategies configured</CardTitle>
            <CardDescription>Create one to get started.</CardDescription>
          </CardHeader>
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {data.strategies.map((strategy) => (
            <Card key={strategy.name}>
              <CardHeader>
                <div className="flex items-center gap-2">
                  <CardTitle className="text-base">{strategy.name}</CardTitle>
                  <Badge className={KIND_STYLES[strategy.kind] ?? ""}>
                    {strategy.kind}
                  </Badge>
                  <Badge className={STAGE_STYLES[strategy.stage] ?? ""}>
                    {strategy.stage}
                  </Badge>
                  <span className="ml-auto text-xs text-muted-foreground">
                    capital ×{strategy.capital_fraction}
                  </span>
                </div>
                <CardDescription>
                  {KIND_LABELS[strategy.kind] ?? strategy.kind}
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
                  {Object.entries(strategy.params)
                    .filter(([, value]) => isEditableParam(value))
                    .map(([key, value]) => (
                      <div key={key} className="flex justify-between gap-3">
                        <span className="text-muted-foreground">{key}</span>
                        <span className="font-mono">{String(value)}</span>
                      </div>
                    ))}
                </div>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    className="gap-1.5"
                    onClick={() => setRunFor(strategy)}
                  >
                    <Play className="h-3.5 w-3.5" /> Backtest
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1.5"
                    onClick={() => {
                      setEditing(strategy);
                      setFormOpen(true);
                    }}
                  >
                    <Pencil className="h-3.5 w-3.5" /> Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1.5 text-rose-600 hover:text-rose-600 dark:text-rose-400"
                    onClick={() => {
                      setDeleteError(null);
                      setDeleting(strategy);
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5" /> Delete
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <StrategyFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        strategy={editing}
        symbols={symbols}
        onSaved={() => mutate()}
      />
      <RunBacktestDialog
        open={runFor !== null}
        onOpenChange={(open) => !open && setRunFor(null)}
        strategy={runFor}
        symbols={symbols}
      />
      <Dialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete {deleting?.name}?</DialogTitle>
            <DialogDescription>
              Removes it from strategies.yaml. Backtest artifacts are kept.
            </DialogDescription>
          </DialogHeader>
          {deleteError ? (
            <p className="rounded-md bg-rose-500/10 px-3 py-2 text-sm text-rose-600 dark:text-rose-400">
              {deleteError}
            </p>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={confirmDelete}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
