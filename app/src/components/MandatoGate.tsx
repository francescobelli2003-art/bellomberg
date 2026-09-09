import { useCallback, useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Bellomberg } from '@/lib/api';
import { dettaglioLeggibile, type StatoMandato } from '@/lib/mandato';

export default function MandatoGate({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate(), location = useLocation();
  const [stato, setStato] = useState<'loading'|'ok'|'fault'>('loading');
  const [motivo, setMotivo] = useState('');
  const verifica = useCallback(async () => {
    setStato('loading'); setMotivo('');
    try {
      const m: StatoMandato = await Bellomberg.mandato();
      setStato('ok');
      if (!m.dichiarato && location.pathname !== '/mandato') navigate('/mandato', { replace: true });
    } catch (e) { setMotivo(dettaglioLeggibile(e)); setStato('fault'); }
  }, [location.pathname, navigate]);
  useEffect(() => { verifica(); }, []);
  if (stato === 'loading') return <div className="mandato-fault"><b>VERIFICA MANDATO…</b></div>;
  if (stato === 'fault') return <div className="mandato-fault" role="alert"><b>MANDATO NON LEGGIBILE</b><pre>{motivo}</pre><p>Il file potrebbe essere in uso o danneggiato. La bozza della sessione resta intatta.</p><button onClick={verifica}>RIPROVA</button></div>;
  return <>{children}</>;
}
