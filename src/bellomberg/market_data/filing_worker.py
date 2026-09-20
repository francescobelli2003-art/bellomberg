"""Worker I-20 schedulabile: scadenze persistenti, log e codici d'uscita espliciti."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


def execute_due(service):
    outcomes = service.run_due()
    bad = [r for r in outcomes if r['status'] not in ('ok', 'skipped')]
    return {'checked_at': datetime.now(timezone.utc).isoformat(),
            'status': 'attention' if bad else 'ok', 'runs': outcomes}


def main(argv=None):
    from bellomberg.core.paths import SQLITE_PATH, DATA_DIR
    from bellomberg.storage.filing_store import FilingStore
    from bellomberg.market_data.filing_service import FilingService
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=str(SQLITE_PATH))
    parser.add_argument('--archive', default=str(DATA_DIR / 'filing_archive'))
    parser.add_argument('--log', default=str(DATA_DIR / 'filing_worker.jsonl'))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--due', action='store_true', help='Esegue i profili abilitati e scaduti (default)')
    modes.add_argument('--status', action='store_true', help='Legge profili e scadenze, nessuna acquisizione')
    modes.add_argument('--ticker', help='Aggiornamento manuale di un profilo configurato')
    modes.add_argument('--recover-run', type=int, help='Conclude un run interrotto; verificare prima che il worker sia fermo')
    parser.add_argument('--reason', help='Motivo obbligatorio del recupero')
    args = parser.parse_args(argv)
    try:
        store = FilingStore(args.db)
        service = FilingService(store, Path(args.archive).resolve())
        if args.status:
            output = {'profiles': store.list_profiles(), 'due': store.next_due()}
        elif args.recover_run is not None:
            output = store.recover_run(args.recover_run, args.reason)
        elif args.ticker:
            output = service.run(args.ticker)
        else:
            output = execute_due(service)
        code = 0 if output.get('status', 'ok') in ('ok', 'skipped') else 1
    except Exception as exc:
        output = {'status': 'errore', 'reason': f'{type(exc).__name__}: {exc}'}
        code = 1
    text = json.dumps(output, ensure_ascii=False, allow_nan=False)
    print(text)
    if not args.status:
        log = Path(args.log)
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('a', encoding='utf-8') as stream:
            stream.write(text + '\n')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
