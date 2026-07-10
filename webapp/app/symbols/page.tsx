import { redirect } from "next/navigation";

import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { apiGet } from "@/lib/api";
import type { SymbolList } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function SymbolsIndexPage() {
  let symbols: string[] = [];
  let failure: string | null = null;
  try {
    symbols = (await apiGet<SymbolList>("/api/symbols")).symbols;
  } catch (error) {
    failure = error instanceof Error ? error.message : "failed to load symbols";
  }
  if (symbols.length > 0) {
    redirect(`/symbols/${symbols[0]}`);
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>No symbols</CardTitle>
        <CardDescription>
          {failure ?? (
            <>
              The store is empty — run{" "}
              <code className="font-mono">finora universe</code> and{" "}
              <code className="font-mono">finora etl</code> first.
            </>
          )}
        </CardDescription>
      </CardHeader>
    </Card>
  );
}
