"use client";

import { CalendarIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DateRange } from "react-day-picker";
import useSWR from "swr";

import {
  CandlestickChart,
  type VisibleRange,
} from "@/components/candlestick-chart";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
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
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { fetcher } from "@/lib/api";
import {
  formatCount,
  formatPrice,
  formatVolume,
  isoDaysAgo,
} from "@/lib/format";
import type { BarsResponse, EventsResponse } from "@/lib/types";

const PRESETS = [
  { key: "1M", days: 31 },
  { key: "6M", days: 183 },
  { key: "1Y", days: 366 },
  { key: "5Y", days: 1827 },
  { key: "MAX", days: null },
] as const;

type PresetKey = (typeof PRESETS)[number]["key"] | "CUSTOM";

const TABLE_LIMIT = 100;

interface Props {
  symbol: string;
  symbols: string[];
}

export function SymbolExplorer({ symbol, symbols }: Props) {
  const router = useRouter();
  const [preset, setPreset] = useState<PresetKey>("1Y");
  const [customRange, setCustomRange] = useState<DateRange | undefined>();
  const [adjusted, setAdjusted] = useState(true);
  const [showDividends, setShowDividends] = useState(true);

  // Full history is always loaded; presets only move the chart's view, so
  // panning left reaches the entire stored series.
  const { data: barsData, error: barsError } = useSWR<BarsResponse>(
    `/api/symbols/${symbol}/bars`,
    fetcher,
  );
  const { data: eventsData } = useSWR<EventsResponse>(
    `/api/symbols/${symbol}/events`,
    fetcher,
  );

  const bars = useMemo(() => barsData?.bars ?? [], [barsData]);
  const events = useMemo(() => eventsData?.events ?? [], [eventsData]);
  const lastDate = bars.length > 0 ? bars[bars.length - 1].date : null;

  const visibleRange = useMemo<VisibleRange | null>(() => {
    if (!lastDate) return null;
    if (preset === "CUSTOM" && customRange?.from) {
      return {
        from: toIso(customRange.from),
        to: customRange.to ? toIso(customRange.to) : lastDate,
      };
    }
    const days = PRESETS.find((p) => p.key === preset)?.days ?? null;
    return days ? { from: isoDaysAgo(days, new Date(lastDate)), to: lastDate } : null;
  }, [preset, customRange, lastDate]);

  // What the user actually sees after panning/zooming (debounced).
  const [view, setView] = useState<VisibleRange | null>(null);
  const viewTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleViewChange = useCallback((from: string, to: string) => {
    if (viewTimer.current) clearTimeout(viewTimer.current);
    viewTimer.current = setTimeout(() => setView({ from, to }), 150);
  }, []);
  useEffect(
    () => () => {
      if (viewTimer.current) clearTimeout(viewTimer.current);
    },
    [],
  );

  const visibleBars = useMemo(() => {
    if (!view) return bars;
    return bars.filter((bar) => bar.date >= view.from && bar.date <= view.to);
  }, [bars, view]);

  const eventCounts = useMemo(() => {
    if (visibleBars.length === 0) return { splits: 0, dividends: 0 };
    const first = visibleBars[0].date;
    const last = visibleBars[visibleBars.length - 1].date;
    const inView = events.filter((e) => e.date >= first && e.date <= last);
    return {
      splits: inView.filter((e) => e.kind === "split").length,
      dividends: inView.filter((e) => e.kind === "dividend").length,
    };
  }, [events, visibleBars]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Select value={symbol} onValueChange={(s) => router.push(`/symbols/${s}`)}>
          <SelectTrigger className="w-32 font-medium">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {symbols.map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          value={preset}
          onValueChange={(value) => value && setPreset(value as PresetKey)}
        >
          {PRESETS.map((p) => (
            <ToggleGroupItem key={p.key} value={p.key}>
              {p.key}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>

        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" size="sm" className="gap-2">
              <CalendarIcon className="h-4 w-4" />
              {preset === "CUSTOM" && customRange?.from
                ? `${toIso(customRange.from)} → ${customRange.to ? toIso(customRange.to) : "…"}`
                : "Custom"}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-0" align="start">
            <Calendar
              mode="range"
              numberOfMonths={2}
              selected={customRange}
              onSelect={(range) => {
                setCustomRange(range);
                if (range?.from) setPreset("CUSTOM");
              }}
            />
          </PopoverContent>
        </Popover>

        <div className="ml-auto flex items-center gap-2">
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            value={adjusted ? "adj" : "raw"}
            onValueChange={(value) => value && setAdjusted(value === "adj")}
          >
            <ToggleGroupItem value="adj">Adjusted</ToggleGroupItem>
            <ToggleGroupItem value="raw">Raw</ToggleGroupItem>
          </ToggleGroup>
          <Button
            variant={showDividends ? "secondary" : "outline"}
            size="sm"
            onClick={() => setShowDividends((v) => !v)}
          >
            Dividends
          </Button>
        </div>
      </div>

      <Card className="py-3">
        <CardContent className="px-3">
          {barsError ? (
            <p className="py-24 text-center text-sm text-muted-foreground">
              {String(barsError.message ?? barsError)}
            </p>
          ) : !barsData ? (
            <Skeleton className="h-[420px] w-full" />
          ) : bars.length === 0 ? (
            <p className="py-24 text-center text-sm text-muted-foreground">
              No bars stored for this symbol.
            </p>
          ) : (
            <CandlestickChart
              bars={bars}
              events={events}
              adjusted={adjusted}
              showDividends={showDividends}
              visibleRange={visibleRange}
              onVisibleRangeChange={handleViewChange}
            />
          )}
          <p className="mt-2 px-1 text-xs text-muted-foreground">
            {barsData
              ? `viewing ${formatCount(visibleBars.length)} of ${formatCount(bars.length)} bars` +
                (visibleBars.length > 0
                  ? ` · ${visibleBars[0].date} → ${visibleBars[visibleBars.length - 1].date}`
                  : "") +
                ` · ${eventCounts.splits} splits, ${eventCounts.dividends} dividends in view` +
                " · drag or zoom to browse the full history"
              : "loading…"}
            {" · "}
            {adjusted
              ? "adjusted = close × factor (splits + dividends)"
              : "raw = as stored (provider applies splits retroactively)"}
          </p>
        </CardContent>
      </Card>

      <Card className="py-0">
        <CardHeader className="pt-4">
          <CardTitle className="text-base">Bars in view</CardTitle>
          <CardDescription>
            {visibleBars.length > TABLE_LIMIT
              ? `latest ${TABLE_LIMIT} of ${formatCount(visibleBars.length)} in view`
              : `${visibleBars.length} rows`}
          </CardDescription>
        </CardHeader>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead className="text-right">Open</TableHead>
                <TableHead className="text-right">High</TableHead>
                <TableHead className="text-right">Low</TableHead>
                <TableHead className="text-right">Close</TableHead>
                <TableHead className="text-right">Volume</TableHead>
                <TableHead className="text-right">Factor</TableHead>
                <TableHead className="text-right">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {visibleBars
                .slice(-TABLE_LIMIT)
                .reverse()
                .map((bar) => (
                  <TableRow key={bar.date}>
                    <TableCell className="tabular-nums">{bar.date}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatPrice(bar.open)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatPrice(bar.high)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatPrice(bar.low)}
                    </TableCell>
                    <TableCell className="text-right font-medium tabular-nums">
                      {formatPrice(bar.close)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatVolume(bar.volume)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {bar.factor.toFixed(4)}
                    </TableCell>
                    <TableCell className="text-right">
                      {bar.split_ratio > 0 ? (
                        <Badge className="bg-amber-500/15 text-amber-700 dark:text-amber-400">
                          split {bar.split_ratio}:1
                        </Badge>
                      ) : bar.dividend > 0 ? (
                        <Badge className="bg-blue-500/15 text-blue-700 dark:text-blue-400">
                          div ${bar.dividend.toFixed(2)}
                        </Badge>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
            </TableBody>
          </Table>
        </div>
      </Card>
    </div>
  );
}

function toIso(date: Date): string {
  return date.toISOString().slice(0, 10);
}
