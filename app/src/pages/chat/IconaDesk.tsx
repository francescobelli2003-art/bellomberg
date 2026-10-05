import { Activity, Bitcoin, FilePenLine, FileSpreadsheet, Landmark, Megaphone, MessageSquareText, Newspaper, Scale, Sigma } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

/** Un'icona per desk, come i loghi dei titoli: cerchio pieno, segno bianco. */
const ICONE: Record<string, LucideIcon> = {
  capo: FilePenLine, macro: Landmark, options: Activity, quant: Sigma, fundamentals: FileSpreadsheet,
  crypto: Bitcoin, eventdesk: Megaphone, politics: Scale, news: Newspaper,
};

/** Tinta del desk (dal colore del backend) alla stessa luminosità delle iniziali dei titoli
 *  (coloreStabile: 46% / 31%), così il segno bianco si legge su Chiaro e su Scuro. */
function tonalita(hex: string | null | undefined): number | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return null;
  const n = Number.parseInt(m[1], 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(v => v / 255);
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  let h = 0;
  if (d) h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return Math.round((h * 60 + 360) % 360);
}

export function coloreDesk(hex: string | null | undefined, ritirato = false): string {
  const h = ritirato ? null : tonalita(hex);
  return h == null ? 'hsl(220 8% 40%)' : `hsl(${h} 46% 31%)`;
}

/** La stessa tinta a luminosita' media, per fili, barre e impulsi: si vede su Chiaro e su Scuro. */
export function tintaDesk(hex: string | null | undefined): string {
  const h = tonalita(hex);
  return h == null ? 'hsl(220 8% 55%)' : `hsl(${h} 58% 55%)`;
}

export default function IconaDesk({ id, colore, ritirato = false, dimensione = 'md', className = '' }: {
  id: string;
  colore?: string | null;
  ritirato?: boolean;
  dimensione?: 'xs' | 'sm' | 'md';
  className?: string;
}) {
  const Icona = ICONE[id] || MessageSquareText;
  const px = dimensione === 'xs' ? 13 : dimensione === 'sm' ? 17 : 22;
  return (
    <span className={`bbn-ico is-${dimensione} bbn-chat-desk-ico${className ? ' ' + className : ''}`}
      style={{ background: coloreDesk(colore, ritirato) }} aria-hidden="true" data-desk-icon={id}>
      <Icona size={px} strokeWidth={2} />
    </span>
  );
}
