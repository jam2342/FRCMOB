export function fetchModels(options: {
  source: string | undefined;
  outDir: string;
  checksums: Record<string, string>;
  fetchImpl?: (url: string) => Promise<{ ok: boolean; status: number; arrayBuffer(): Promise<ArrayBuffer> }>;
}): Promise<string[]>;
