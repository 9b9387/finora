// Per-kind parameter forms. Fields not listed here (e.g. qlib segment
// arrays) are preserved untouched when editing.

export interface ParamField {
  key: string;
  label: string;
  type: "symbol" | "int" | "float" | "text";
  hint?: string;
}

export const KIND_LABELS: Record<string, string> = {
  rsi: "RSI overbought/oversold",
  ma_cross: "Double MA crossover",
  bollinger: "Bollinger breakout",
  momentum: "Cross-sectional momentum",
  qlib: "Qlib model",
};

export const CREATABLE_KINDS = ["rsi", "ma_cross", "bollinger", "momentum"] as const;

export const TECHNICAL_KINDS = ["rsi", "ma_cross", "bollinger"];

export const KIND_PARAM_FIELDS: Record<string, ParamField[]> = {
  rsi: [
    { key: "symbol", label: "Symbol", type: "symbol" },
    { key: "period", label: "RSI period", type: "int", hint: "daily Wilder RSI" },
    { key: "buy_below", label: "Buy below", type: "float", hint: "oversold trigger" },
    { key: "sell_above", label: "Sell above", type: "float", hint: "overbought trigger" },
    { key: "rearm", label: "Re-arm level", type: "float", hint: "trigger resets here" },
    { key: "unit_fraction", label: "Unit fraction", type: "float", hint: "weight per trade" },
    { key: "max_units", label: "Max units", type: "int", hint: "position cap" },
  ],
  ma_cross: [
    { key: "symbol", label: "Symbol", type: "symbol" },
    { key: "fast_days", label: "Fast SMA days", type: "int" },
    { key: "slow_days", label: "Slow SMA days", type: "int" },
    { key: "weight", label: "Weight", type: "float", hint: "equity fraction while long" },
  ],
  bollinger: [
    { key: "symbol", label: "Symbol", type: "symbol" },
    { key: "period", label: "Band period", type: "int", hint: "middle band SMA" },
    { key: "num_std", label: "Std devs", type: "float", hint: "upper band offset" },
    { key: "weight", label: "Weight", type: "float", hint: "equity fraction while long" },
  ],
  momentum: [
    { key: "lookback_days", label: "Lookback days", type: "int" },
    { key: "top_k", label: "Top K", type: "int", hint: "names held" },
  ],
  qlib: [
    { key: "top_k", label: "Top K", type: "int" },
    { key: "n_drop", label: "N drop", type: "int" },
    { key: "benchmark", label: "Benchmark", type: "symbol" },
    { key: "model_dir", label: "Model dir", type: "text" },
  ],
};

export const KIND_PARAM_DEFAULTS: Record<string, Record<string, unknown>> = {
  rsi: {
    symbol: "SPY", period: 14, buy_below: 30, sell_above: 70,
    rearm: 50, unit_fraction: 0.25, max_units: 4,
  },
  ma_cross: { symbol: "SPY", fast_days: 50, slow_days: 200, weight: 1.0 },
  bollinger: { symbol: "SPY", period: 20, num_std: 2.0, weight: 1.0 },
  momentum: { lookback_days: 126, top_k: 20 },
};
