"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiSend } from "@/lib/api";
import { TECHNICAL_KINDS } from "@/lib/strategy-fields";
import type { RunBacktestResponse, StrategyModel } from "@/lib/types";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  strategy: StrategyModel | null;
  symbols: string[];
}

export function RunBacktestDialog({ open, onOpenChange, strategy, symbols }: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        {open && strategy ? (
          <RunBody
            key={strategy.name}
            strategy={strategy}
            symbols={symbols}
            onClose={() => onOpenChange(false)}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function RunBody({
  strategy,
  symbols,
  onClose,
}: {
  strategy: StrategyModel;
  symbols: string[];
  onClose: () => void;
}) {
  const router = useRouter();
  const technical = TECHNICAL_KINDS.includes(strategy.kind);
  const [symbol, setSymbol] = useState(String(strategy.params["symbol"] ?? ""));
  const [start, setStart] = useState("2024-01-01");
  const [end, setEnd] = useState("");
  const [costBps, setCostBps] = useState("15");
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      const result = await apiSend<RunBacktestResponse>("POST", "/api/backtests/run", {
        name: strategy.name,
        symbol: technical && symbol.trim() !== "" ? symbol.trim().toUpperCase() : null,
        start: start || null,
        end: end || null,
        cost_bps: Number.parseFloat(costBps) || 15.0,
      });
      onClose();
      router.push(`/backtests/${result.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setRunning(false);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>Backtest {strategy.name}</DialogTitle>
        <DialogDescription>
          Runs against the local store and opens the result. Costs apply per
          traded fraction.
        </DialogDescription>
      </DialogHeader>

      <div className="grid gap-4">
        {technical ? (
          <div className="grid gap-1.5">
            <Label htmlFor="bt-symbol">Symbol</Label>
            <Input
              id="bt-symbol"
              list="bt-symbol-options"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            />
            <datalist id="bt-symbol-options">
              {symbols.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
            <p className="text-xs text-muted-foreground">
              Any stored symbol — a different one runs under a derived name so
              the strategy&apos;s own artifact is kept.
            </p>
          </div>
        ) : null}

        <div className="grid grid-cols-2 gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="bt-start">Start</Label>
            <Input
              id="bt-start"
              type="date"
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="bt-end">End (optional)</Label>
            <Input
              id="bt-end"
              type="date"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </div>
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="bt-cost">Cost (bps per traded fraction)</Label>
          <Input
            id="bt-cost"
            type="number"
            step="1"
            min="0"
            value={costBps}
            onChange={(e) => setCostBps(e.target.value)}
          />
        </div>

        {error ? (
          <p className="rounded-md bg-rose-500/10 px-3 py-2 text-sm text-rose-600 dark:text-rose-400">
            {error}
          </p>
        ) : null}
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={running}>
          Cancel
        </Button>
        <Button onClick={run} disabled={running}>
          {running ? "Running…" : "Run backtest"}
        </Button>
      </DialogFooter>
    </>
  );
}
