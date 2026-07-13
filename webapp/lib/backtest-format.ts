export function pct(value: number | null, digits = 2): string {
  if (value == null) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(digits)}%`;
}

export function num(value: number | null, digits = 2): string {
  return value == null ? "—" : value.toFixed(digits);
}

export function signClass(value: number | null): string {
  if (value == null || value === 0) return "";
  return value > 0
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-rose-600 dark:text-rose-400";
}

/** e.g. "20260709" -> "2026-07-09" */
export function stampToDate(stamp: string): string {
  return /^\d{8}$/.test(stamp)
    ? `${stamp.slice(0, 4)}-${stamp.slice(4, 6)}-${stamp.slice(6)}`
    : stamp;
}
