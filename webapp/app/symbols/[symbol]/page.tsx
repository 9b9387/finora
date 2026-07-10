import { SymbolExplorer } from "@/components/symbol-explorer";
import { apiGet } from "@/lib/api";
import type { SymbolList } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function SymbolPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  let symbols: string[] = [];
  try {
    symbols = (await apiGet<SymbolList>("/api/symbols")).symbols;
  } catch {
    // explorer surfaces API errors itself via SWR
  }
  const upper = decodeURIComponent(symbol).toUpperCase();
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{upper}</h1>
        <p className="text-sm text-muted-foreground">
          Daily bars from the local store; split and dividend markers from the
          provider&apos;s corporate-action data.
        </p>
      </div>
      <SymbolExplorer symbol={upper} symbols={symbols.length ? symbols : [upper]} />
    </div>
  );
}
