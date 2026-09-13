import { linguaCorrente, type Lingua } from '../i18n/lingua';
import { tDinamica, traduci, type Chiave, type Parametri } from '../i18n/t';
import { coperturaCampo, dettaglioLeggibile, type CampoMandato } from './mandato';
import { localizePayload } from './api-presentation';

export type Notice = string | { key: Chiave; params?: Parametri } | { error: unknown };
export function errorForLanguage(error: unknown, language: Lingua) {
  const e = error as any;
  if (e?.response?.data) return dettaglioLeggibile({ ...e, response: { ...e.response, data: localizePayload(e.response.data, language) } });
  return dettaglioLeggibile(error);
}
export const showNotice = (value: Notice | null, language: Lingua) =>
  !value ? '' : typeof value === 'string' ? value : 'error' in value ? errorForLanguage(value.error, language) : traduci(language, value.key, value.params);
export const fieldLabel = (name: string, language: Lingua = linguaCorrente()) => tDinamica(language, 'mandate.field_' + name);
export const sectionLabel = (name: string, language: Lingua = linguaCorrente()) => tDinamica(language, 'mandate.' + name);
// Closed schema vocabularies: labels change, submitted option values do not.
const choices: Record<string, [string, string]> = {
  long_term: ['Lungo termine', 'Long term'], medio_termine: ['Medio termine', 'Medium term'], trading: ['Operatività di breve periodo', 'Short-term trading'],
  concentrato: ['Concentrato', 'Concentrated'], diversificato: ['Diversificato', 'Diversified'],
  prudente: ['Prudente', 'Prudent'], neutra: ['Neutra', 'Neutral'], aggressiva: ['Aggressiva', 'Aggressive'],
  long_call_catalyst: ['Acquisto call con catalizzatore', 'Long call with a catalyst'], put_hedge: ['Put di copertura', 'Protective put'],
  put_spread: ['Differenziale di put', 'Put spread'], covered_call: ['Call coperta', 'Covered call'], cash_secured_put: ['Put garantita dalla cassa', 'Cash-secured put'],
  short_premium_nudo: ['Vendita di premio nuda', 'Naked short premium'], straddle_strangle: ['Straddle o strangle', 'Straddle or strangle'],
};
const cities: Record<string, [string, string]> = { MI:['Milano','Milan'], L:['Londra','London'], DE:['Francoforte','Frankfurt'], FRA:['Francoforte','Frankfurt'],
  PA:['Parigi','Paris'], AS:['Amsterdam','Amsterdam'], BR:['Bruxelles','Brussels'], MC:['Madrid','Madrid'], SW:['Zurigo','Zurich'], VI:['Vienna','Vienna'],
  US:['New York','New York'], TO:['Toronto','Toronto'], T:['Tokyo','Tokyo'], HK:['Hong Kong','Hong Kong'], NS:['Mumbai','Mumbai'], SA:['San Paolo','Sao Paulo'], AX:['Sydney','Sydney'] };
export function optionLabel(value: string, language: Lingua = linguaCorrente()) {
  if (cities[value]) return `${cities[value][language === 'it' ? 0 : 1]} (${value})`;
  if (choices[value]) return choices[value][language === 'it' ? 0 : 1];
  return `⟦${value}⟧`; // A new schema vocabulary needs an explicit label.
}
export function coverageLabel(name: string, language: Lingua = linguaCorrente()) {
  const classes = { motore: 'engine', validazione: 'validation', informativo: 'info', prompt: 'prompt' };
  const coverage = coperturaCampo(name), key = classes[coverage.classe as keyof typeof classes];
  return { ...coverage, etichetta: tDinamica(language, 'mandate.coverage_' + key),
    nota: tDinamica(language, 'mandate.coverage_' + key + '_note') };
}
export function sectionStatus(schema: Record<string, CampoMandato>, form: Record<string, any>, errors: Record<string, string>, section: string) {
  const fields = Object.entries(schema).filter(([, c]) => c.blocco === section);
  const required = fields.filter(([, c]) => c.obbligatorio);
  const missing = required.filter(([name, c]) => {
    const value = form[name];
    if (value == null || value === '') return true;
    if (Array.isArray(value)) return !value.length || value.some(v => v == null || v === '');
    if (c.tipo === 'interruttori') return (c.scelte || []).some(k => typeof value[k] !== 'boolean');
    return false;
  }).length;
  return { fields: fields.length, required: required.length, missing,
    errors: fields.filter(([name]) => !!errors[name]).length };
}
