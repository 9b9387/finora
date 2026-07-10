const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  } catch {
    throw new ApiError(
      0,
      `Cannot reach the data API at ${API_BASE} — is \`finora serve\` running?`,
    );
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      // non-JSON error body; statusText is fine
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

// SWR-compatible fetcher
export const fetcher = <T,>(path: string) => apiGet<T>(path);
