"use client";

import { useMemo, useState } from "react";

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { apiSend } from "@/lib/api";
import {
  CREATABLE_KINDS,
  KIND_LABELS,
  KIND_PARAM_DEFAULTS,
  KIND_PARAM_FIELDS,
} from "@/lib/strategy-fields";
import type { StrategyModel } from "@/lib/types";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** null = create a new strategy */
  strategy: StrategyModel | null;
  symbols: string[];
  onSaved: () => void;
}

const STAGES = ["paper", "small", "full"] as const;

export function StrategyFormDialog({
  open,
  onOpenChange,
  strategy,
  symbols,
  onSaved,
}: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        {open ? (
          // keyed remount: state initializers run fresh for each subject
          <FormBody
            key={strategy?.name ?? "__new__"}
            strategy={strategy}
            symbols={symbols}
            onSaved={onSaved}
            onClose={() => onOpenChange(false)}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function FormBody({
  strategy,
  symbols,
  onSaved,
  onClose,
}: {
  strategy: StrategyModel | null;
  symbols: string[];
  onSaved: () => void;
  onClose: () => void;
}) {
  const editing = strategy !== null;
  const [name, setName] = useState(strategy?.name ?? "");
  const [kind, setKind] = useState(strategy?.kind ?? "rsi");
  const [stage, setStage] = useState(strategy?.stage ?? "paper");
  const [capitalFraction, setCapitalFraction] = useState(
    String(strategy?.capital_fraction ?? 1.0),
  );
  const [paramText, setParamText] = useState<Record<string, string>>(() =>
    stringifyParams(
      strategy?.kind ?? "rsi",
      strategy?.params ?? KIND_PARAM_DEFAULTS["rsi"],
    ),
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const fields = useMemo(() => KIND_PARAM_FIELDS[kind] ?? [], [kind]);

  const switchKind = (next: string) => {
    setKind(next);
    setParamText(stringifyParams(next, KIND_PARAM_DEFAULTS[next] ?? {}));
  };

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      const params: Record<string, unknown> = { ...(strategy?.params ?? {}) };
      for (const field of fields) {
        const raw = (paramText[field.key] ?? "").trim();
        if (raw === "") continue;
        if (field.type === "int") {
          params[field.key] = Number.parseInt(raw, 10);
        } else if (field.type === "float") {
          params[field.key] = Number.parseFloat(raw);
        } else if (field.type === "symbol") {
          params[field.key] = raw.toUpperCase();
        } else {
          params[field.key] = raw;
        }
      }
      const body = {
        name: name.trim(),
        kind,
        stage,
        capital_fraction: Number.parseFloat(capitalFraction),
        params,
      };
      if (editing) {
        await apiSend("PUT", `/api/strategies/${encodeURIComponent(body.name)}`, body);
      } else {
        await apiSend("POST", "/api/strategies", body);
      }
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>{editing ? `Edit ${strategy.name}` : "New strategy"}</DialogTitle>
        <DialogDescription>
          Saved to <code className="font-mono">config/strategies.yaml</code> — the
          CLI sees the same configuration.
        </DialogDescription>
      </DialogHeader>

      <div className="grid gap-4">
        <div className="grid grid-cols-2 gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="strategy-name">Name</Label>
            <Input
              id="strategy-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="rsi_aapl"
              disabled={editing}
            />
          </div>
          <div className="grid gap-1.5">
            <Label>Kind</Label>
            <Select value={kind} onValueChange={switchKind} disabled={editing}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(editing ? [kind] : [...CREATABLE_KINDS]).map((k) => (
                  <SelectItem key={k} value={k}>
                    {KIND_LABELS[k] ?? k}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="grid gap-1.5">
            <Label>Stage</Label>
            <Select value={stage} onValueChange={setStage}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STAGES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="capital-fraction">Capital fraction</Label>
            <Input
              id="capital-fraction"
              type="number"
              step="0.05"
              min="0.05"
              max="1"
              value={capitalFraction}
              onChange={(e) => setCapitalFraction(e.target.value)}
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          {fields.map((field) => (
            <div key={field.key} className="grid gap-1.5">
              <Label htmlFor={`param-${field.key}`}>{field.label}</Label>
              {field.type === "symbol" ? (
                <>
                  <Input
                    id={`param-${field.key}`}
                    list="symbol-options"
                    value={paramText[field.key] ?? ""}
                    onChange={(e) =>
                      setParamText((prev) => ({
                        ...prev,
                        [field.key]: e.target.value.toUpperCase(),
                      }))
                    }
                  />
                  <datalist id="symbol-options">
                    {symbols.map((s) => (
                      <option key={s} value={s} />
                    ))}
                  </datalist>
                </>
              ) : (
                <Input
                  id={`param-${field.key}`}
                  type={field.type === "text" ? "text" : "number"}
                  step={field.type === "float" ? "0.05" : "1"}
                  value={paramText[field.key] ?? ""}
                  onChange={(e) =>
                    setParamText((prev) => ({ ...prev, [field.key]: e.target.value }))
                  }
                />
              )}
              {field.hint ? (
                <p className="text-xs text-muted-foreground">{field.hint}</p>
              ) : null}
            </div>
          ))}
        </div>

        {error ? (
          <p className="rounded-md bg-rose-500/10 px-3 py-2 text-sm text-rose-600 dark:text-rose-400">
            {error}
          </p>
        ) : null}
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={submit} disabled={saving || name.trim() === ""}>
          {saving ? "Saving…" : editing ? "Save changes" : "Create strategy"}
        </Button>
      </DialogFooter>
    </>
  );
}

function stringifyParams(
  kind: string,
  params: Record<string, unknown>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const field of KIND_PARAM_FIELDS[kind] ?? []) {
    const value = params[field.key] ?? KIND_PARAM_DEFAULTS[kind]?.[field.key];
    out[field.key] = value == null ? "" : String(value);
  }
  return out;
}
