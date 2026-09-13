import { linguaCorrente, type Lingua } from '../i18n/lingua';

/** Render only variants explicitly authored by the backend. No network or data calculation. */
export function localizePayload<T>(payload: T, language: Lingua = linguaCorrente()): T {
  if (payload === null || typeof payload !== 'object' || !Object.prototype.hasOwnProperty.call(payload, '_presentation_v1')) return payload;
  const bad = () => new Error(language === 'it' ? 'Metadati di presentazione non validi: vista non verificata.' : 'Invalid presentation metadata: view not verified.');
  const metadata = (payload as any)._presentation_v1;
  if ((language !== 'it' && language !== 'en') || !metadata || metadata.version !== 1 || !Array.isArray(metadata.texts)) throw bad();
  const seen = new Set<string>();
  // Copy only branches containing authored text. Unchanged chart arrays keep
  // their identity, preventing language selection from replacing series data.
  const copies = new WeakMap<object, any>();
  const cloneBranch = (value: any) => {
    if (copies.has(value)) return copies.get(value);
    const cloned = Array.isArray(value) ? [...value] : { ...value };
    copies.set(value, cloned); copies.set(cloned, cloned);
    return cloned;
  };
  const copy = cloneBranch(payload);
  for (const item of metadata.texts) {
    if (!item || !Array.isArray(item.path) || item.path.length === 0 || typeof item.it !== 'string' || typeof item.en !== 'string') throw bad();
    const identity = JSON.stringify(item.path);
    if (seen.has(identity) || item.path[0] === '_presentation_v1') throw bad();
    seen.add(identity);
    let parent: any = copy;
    for (const [index, key] of item.path.entries()) {
      if (!(typeof key === 'string' || (Number.isInteger(key) && key >= 0)) ||
          key === '__proto__' || key === 'prototype' || key === 'constructor' ||
          parent === null || typeof parent !== 'object' || !Object.prototype.hasOwnProperty.call(parent, key)) throw bad();
      if (index === item.path.length - 1) {
        // Metadata can replace only its own exact authored string, never a measure.
        if (typeof parent[key] !== 'string' || (parent[key] !== item.it && parent[key] !== item.en)) throw bad();
        parent[key] = item[language];
      } else {
        if (parent[key] === null || typeof parent[key] !== 'object') throw bad();
        parent[key] = cloneBranch(parent[key]);
        parent = parent[key];
      }
    }
  }
  return copy;
}
