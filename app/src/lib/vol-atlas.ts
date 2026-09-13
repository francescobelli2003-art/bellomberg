/** Presentation only: never alter acquisition scope, raw rows or financial values. */
export type VolWorkspace = 'acquisition' | 'tools' | 'chain' | 'laboratory';
export function surfaceExpiries(surface: any): string[] {
  return [...new Set<string>((surface?.slices || []).map((row: any) => row.expiry))].sort();
}
export function visibleSurface(surface: any, selected: string[] | null): any {
  if (!surface || selected === null) return surface;
  const keep = new Set(selected);
  return { ...surface, slices: (surface.slices || []).filter((row: any) => keep.has(row.expiry)),
    term_structure: (surface.term_structure || []).filter((row: any) => keep.has(row.expiry)) };
}
export function toggleExpiry(selected: string[] | null, available: string[], expiry: string): string[] {
  if (!available.includes(expiry)) throw new Error('unknown display expiry');
  const current = selected === null ? available : selected;
  const next = new Set(current);
  if (next.has(expiry)) next.delete(expiry); else next.add(expiry);
  return available.filter(date => next.has(date));
}
