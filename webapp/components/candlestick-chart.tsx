"use client";

import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { useTheme } from "next-themes";
import { useEffect, useMemo, useRef } from "react";

import type { AdjustmentEvent, Bar } from "@/lib/types";

export interface VisibleRange {
  from: string;
  to: string;
}

interface Props {
  bars: Bar[];
  events: AdjustmentEvent[];
  adjusted: boolean;
  showDividends: boolean;
  /** Window to show initially / on preset change; null fits all data. The
   *  user can always pan and zoom beyond it — the full history is loaded. */
  visibleRange: VisibleRange | null;
  /** Fires (debounced by the caller) as the user pans or zooms. */
  onVisibleRangeChange?: (from: string, to: string) => void;
  height?: number;
}

const PALETTES = {
  light: {
    text: "#71717a",
    grid: "rgba(0, 0, 0, 0.06)",
    border: "rgba(0, 0, 0, 0.12)",
    up: "#059669",
    down: "#e11d48",
    volumeUp: "rgba(5, 150, 105, 0.35)",
    volumeDown: "rgba(225, 29, 72, 0.35)",
    split: "#d97706",
    dividend: "#2563eb",
  },
  dark: {
    text: "#a1a1aa",
    grid: "rgba(255, 255, 255, 0.07)",
    border: "rgba(255, 255, 255, 0.15)",
    up: "#34d399",
    down: "#fb7185",
    volumeUp: "rgba(52, 211, 153, 0.35)",
    volumeDown: "rgba(251, 113, 133, 0.35)",
    split: "#fbbf24",
    dividend: "#60a5fa",
  },
} as const;

function toTime(date: string): Time {
  return date as Time; // 'YYYY-MM-DD' business day strings are valid Time values
}

function timeToIso(value: Time): string {
  if (typeof value === "string") return value;
  if (typeof value === "number") {
    return new Date(value * 1000).toISOString().slice(0, 10);
  }
  const { year, month, day } = value;
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

export function CandlestickChart({
  bars,
  events,
  adjusted,
  showDividends,
  visibleRange,
  onVisibleRangeChange,
  height = 420,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const priceRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  // latest callback without re-creating the chart subscription
  const rangeCallbackRef = useRef(onVisibleRangeChange);
  useEffect(() => {
    rangeCallbackRef.current = onVisibleRangeChange;
  }, [onVisibleRangeChange]);

  const { resolvedTheme } = useTheme();
  const palette = PALETTES[resolvedTheme === "dark" ? "dark" : "light"];

  const candles = useMemo(
    () =>
      bars.map((bar) => {
        const scale = adjusted ? bar.factor : 1;
        const open = (bar.open ?? bar.close) * scale;
        const high = (bar.high ?? bar.close) * scale;
        const low = (bar.low ?? bar.close) * scale;
        return {
          time: toTime(bar.date),
          open,
          high: Math.max(high, open),
          low: Math.min(low, open),
          close: bar.close * scale,
        };
      }),
    [bars, adjusted],
  );

  const volumes = useMemo(
    () =>
      bars.map((bar) => ({
        time: toTime(bar.date),
        value: bar.volume ?? 0,
        color:
          bar.close >= (bar.open ?? bar.close)
            ? palette.volumeUp
            : palette.volumeDown,
      })),
    [bars, palette],
  );

  const markers = useMemo(() => {
    if (bars.length === 0) return [];
    const first = bars[0].date;
    const last = bars[bars.length - 1].date;
    const visible = events.filter(
      (event) =>
        event.date >= first &&
        event.date <= last &&
        (event.kind === "split" || showDividends),
    );
    return visible.map<SeriesMarker<Time>>((event) => ({
      time: toTime(event.date),
      position: event.kind === "split" ? "belowBar" : "aboveBar",
      shape: event.kind === "split" ? "arrowUp" : "circle",
      color: event.kind === "split" ? palette.split : palette.dividend,
      size: event.kind === "split" ? 2 : 0.7,
      text:
        event.kind === "split"
          ? `S ${formatSplit(event.split_ratio ?? 0)}`
          : `D ${event.dividend?.toFixed(2) ?? ""}`,
    }));
  }, [bars, events, showDividends, palette]);

  // Create the chart once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const chart = createChart(container, { height, autoSize: true });
    const price = chart.addSeries(CandlestickSeries, {
      borderVisible: false,
      priceLineVisible: false,
    });
    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: "volume",
      priceFormat: { type: "volume" },
      priceLineVisible: false,
      lastValueVisible: false,
    });
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    });
    chart.priceScale("right").applyOptions({
      scaleMargins: { top: 0.06, bottom: 0.2 },
    });
    chartRef.current = chart;
    priceRef.current = price;
    volumeRef.current = volume;
    markersRef.current = createSeriesMarkers(price, []);
    chart.timeScale().subscribeVisibleTimeRangeChange((range) => {
      if (range) {
        rangeCallbackRef.current?.(timeToIso(range.from), timeToIso(range.to));
      }
    });
    return () => {
      markersRef.current = null;
      priceRef.current = null;
      volumeRef.current = null;
      chartRef.current = null;
      chart.remove();
    };
  }, [height]);

  // Data updates.
  useEffect(() => {
    priceRef.current?.setData(candles);
    volumeRef.current?.setData(volumes);
    markersRef.current?.setMarkers(markers);
    applyRange(chartRef.current, visibleRange);
    // visibleRange is applied by its own effect; here it only positions the
    // initial view right after (re)loading data.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles, volumes, markers]);

  // View-window updates (presets / custom range). Panning past the window is
  // always possible because the series holds the full history.
  useEffect(() => {
    if (candles.length > 0) applyRange(chartRef.current, visibleRange);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleRange]);

  // Theme updates.
  useEffect(() => {
    chartRef.current?.applyOptions({
      layout: {
        background: { color: "transparent" },
        textColor: palette.text,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: palette.grid },
        horzLines: { color: palette.grid },
      },
      rightPriceScale: { borderColor: palette.border },
      timeScale: { borderColor: palette.border },
    });
    priceRef.current?.applyOptions({
      upColor: palette.up,
      downColor: palette.down,
      wickUpColor: palette.up,
      wickDownColor: palette.down,
    });
  }, [palette]);

  return <div ref={containerRef} style={{ height }} />;
}

function applyRange(chart: IChartApi | null, range: VisibleRange | null): void {
  if (!chart) return;
  if (range) {
    chart.timeScale().setVisibleRange({
      from: toTime(range.from),
      to: toTime(range.to),
    });
  } else {
    chart.timeScale().fitContent();
  }
}

function formatSplit(ratio: number): string {
  if (ratio >= 1) return `${trimZeros(ratio)}:1`;
  return `1:${trimZeros(1 / ratio)}`;
}

function trimZeros(value: number): string {
  return Number(value.toFixed(2)).toString();
}
