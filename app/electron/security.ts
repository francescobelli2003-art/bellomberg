/** Only web links may reach an OS protocol handler. */
export function externalWebUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return (url.protocol === 'https:' || url.protocol === 'http:') && !url.username && !url.password
      ? url.href : null;
  } catch { return null; }
}

/** HashRouter may change the fragment, but not the document or origin. */
export function isAppDocument(value: string, entry: string): boolean {
  try {
    const target = new URL(value);
    const allowed = new URL(entry);
    return target.protocol === allowed.protocol && target.host === allowed.host
      && target.pathname === allowed.pathname && target.search === allowed.search;
  } catch { return false; }
}

export function apiPort(value: string | undefined): number {
  if (value === undefined || value === '') return 8765;
  const n = Number(value);
  if (!/^\d+$/.test(value) || !Number.isInteger(n) || n < 1024 || n > 65535) {
    throw new Error('BELLOMBERG_API_PORT must be an integer between 1024 and 65535');
  }
  return n;
}

/** Source installs on POSIX keep their dependencies in the backend virtual environment. */
export function defaultPython(platform: string, backendRoot: string, exists: (file: string) => boolean):
  { python: string; source: string; tried: string[] } {
  if (platform === 'win32') return { python: 'python', source: 'PATH', tried: [] };
  const venv = backendRoot.replace(/\/+$/, '') + '/.venv/bin/python';
  if (exists(venv)) return { python: venv, source: 'venv', tried: [] };
  return { python: 'python3', source: 'PATH', tried: [venv] };
}
