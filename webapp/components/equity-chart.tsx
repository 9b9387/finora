"use client";

import {
  AreaSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { useTheme } from "next-themes";
import { useEffect, useMemo, useRef } from "react";

import type { EquityPoint } from "@/lib/types";

const PALETTES = {
  light: {
    text: "#71717a",
    grid: "rgba(0, 0, 0, 0.06)",
    border: "rgba(0, 0, 0, 0.12)",
    equity: "#2563eb",
    equityFill: "rgba(37, 99, 235, 0.12)",
    drawdown: "#e11d48",
    drawdownFill: "rgba(225, 29, 72, 0.15)",
  },
  dark: {
    text: "#a1a1aa",
    grid: "rgba(255, 255, 255, 0.07)",
    border: "rgba(255, 255, 255, 0.15)",
    equity: "#60a5fa",
    equityFill: "rgba(96, 165, 250, 0.15)",
    drawdown: "#fb7185",
    drawdownFill: "rgba(251, 113, 133, 0.18)",
  },
} as const;

function useAreaChart(
  height: number,
  data: { time: Time; value: number }[],
  colorKey: "equity" | "drawdown",
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  const { resolvedTheme } = useTheme();
  const palette = PALETTES[resolvedTheme === "dark" ? "dark" : "light"];

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const chart = createChart(container, { height, autoSize: true });
    const series = chart.addSeries(AreaSeries, {
      lineWidth: 2,
      priceLineVisible: false,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    return () => {
      seriesRef.current = null;
      chartRef.current = null;
      chart.remove();
    };
  }, [height]);

  useEffect(() => {
    seriesRef.current?.setData(data);
    chartRef.current?.timeScale().fitContent();
  }, [data]);

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
    seriesRef.current?.applyOptions({
      lineColor: palette[colorKey],
      topColor: palette[`${colorKey}Fill`],
      bottomColor: "transparent",
    });
  }, [palette, colorKey]);

  return containerRef;
}

export function EquityChart({
  points,
  height = 300,
}: {
  points: EquityPoint[];
  height?: number;
}) {
  const data = useMemo(
    () => points.map((p) => ({ time: p.date as Time, value: p.equity })),
    [points],
  );
  const ref = useAreaChart(height, data, "equity");
  return <div ref={ref} style={{ height }} />;
}

export function DrawdownChart({
  points,
  height = 160,
}: {
  points: EquityPoint[];
  height?: number;
}) {
  const data = useMemo(
    () => points.map((p) => ({ time: p.date as Time, value: p.drawdown * 100 })),
    [points],
  );
  const ref = useAreaChart(height, data, "drawdown");
  return <div ref={ref} style={{ height }} />;
}
