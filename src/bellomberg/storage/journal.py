"""Diario privato: testo utente e versioni nello SQLite di MemoryDB.

Nessuna inizializzazione al read, nessun indice semantico o ingresso nei prompt.
JOURNAL_MIGRATION va registrata nel percorso versionato di MemoryDB.
"""
from bellomberg.core.presentation import message as _ui_text
from contextlib import contextmanager
from datetime import datetime, timezone
import re
import sqlite3

TITLE_LIMIT = 160
BODY_LIMIT = 30000
TICKER_LIMIT = 32
_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.^=_:/-]{0,31}$")
_FIELDS = "id,kind,ticker,title,body,origin,created_at,updated_at,version,archived_at"

JOURNAL_MIGRATION = (8, "Diario utente: note private e revisioni immutabili, archivio reversibile", [
    """CREATE TABLE IF NOT EXISTS journal_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL CHECK(kind IN ('thesis','macro')),
        ticker TEXT,
        title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 160),
        body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 30000),
        origin TEXT NOT NULL CHECK(origin = 'user'),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version >= 1),
        archived_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS journal_revisions (
        entry_id INTEGER NOT NULL REFERENCES journal_entries(id),
        version INTEGER NOT NULL CHECK(version >= 1),
        kind TEXT NOT NULL CHECK(kind IN ('thesis','macro')),
        ticker TEXT,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        origin TEXT NOT NULL CHECK(origin = 'user'),
        created_at TEXT NOT NULL,
        saved_at TEXT NOT NULL,
        archived_at TEXT,
        action TEXT NOT NULL CHECK(action IN ('create','update','archive','restore')),
        PRIMARY KEY(entry_id,version)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_journal_updated ON journal_entries(updated_at DESC,id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_journal_ticker ON journal_entries(ticker,updated_at DESC)",
])


class JournalInvalid(ValueError):
    pass


class JournalMissing(LookupError):
    pass


class JournalConflict(Exception):
    def __init__(self, current_version):
        self.current_version = current_version
        super().__init__(_ui_text('La nota è cambiata. La bozza non è stata salvata: confronta la versione corrente.', 'The note has changed. Your draft was not saved: compare the current version.'))


class JournalUnavailable(Exception):
    def __init__(self, code="journal_unavailable"):
        self.code = code
        message = (_ui_text('Diario non inizializzato: manca la migrazione del database.', 'Journal not initialized: the database migration is missing.')
                   if code == "journal_schema_missing" else
                   _ui_text('Diario non disponibile: lettura o scrittura del database non riuscita.', 'Journal unavailable: database read or write failed.'))
        super().__init__(message)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _text(value, name, maximum, required=True):
    if not isinstance(value, str) or "\x00" in value:
        raise JournalInvalid(_ui_text('{name}: testo non valido', '{name}: invalid text', name=name))
    if len(value) > maximum or (required and not value.strip()):
        raise JournalInvalid(_ui_text('{name}: inserisci da 1 a {maximum} caratteri', '{name}: enter 1 to {maximum} characters', name=name, maximum=maximum))
    return value


def _ticker(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise JournalInvalid(_ui_text('Ticker non valido', 'Invalid ticker'))
    value = value.strip().upper()
    if not _TICKER.fullmatch(value):
        raise JournalInvalid(_ui_text('Ticker non valido: massimo 32 caratteri, senza spazi', 'Invalid ticker: up to 32 characters, without spaces'))
    return value


def _content(kind, ticker, title, body):
    if kind not in ("thesis", "macro"):
        raise JournalInvalid(_ui_text('Tipo nota non valido', 'Invalid note type'))
    return kind, _ticker(ticker), _text(title, _ui_text('Titolo', 'Title'), TITLE_LIMIT).strip(), _text(body, _ui_text('Nota', 'Note'), BODY_LIMIT)


def _positive(value, name):
    if type(value) is not int or value < 1:
        raise JournalInvalid(_ui_text('{name}: intero positivo richiesto', '{name}: positive integer required', name=name))


def _page(limit, offset):
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise JournalInvalid(_ui_text('Paginazione non valida', 'Invalid pagination'))


class JournalStore:
    """Riceve MemoryDB, non crea database o risolve percorsi alternativi."""

    def __init__(self, db):
        self.db = db

    @contextmanager
    def _connection(self):
        try:
            with self.db._conn() as conn:
                yield conn
        except sqlite3.Error as exc:
            code = ("journal_schema_missing" if "no such table: journal_" in str(exc)
                    else "journal_unavailable")
            raise JournalUnavailable(code) from exc

    @staticmethod
    def _get(conn, entry_id):
        _positive(entry_id, _ui_text('ID nota', 'Note ID'))
        row = conn.execute(f"SELECT {_FIELDS} FROM journal_entries WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise JournalMissing(_ui_text('Nota non trovata', 'Note not found'))
        return dict(row)

    @staticmethod
    def _record(conn, entry, action):
        conn.execute("""INSERT INTO journal_revisions
            (entry_id,version,kind,ticker,title,body,origin,created_at,saved_at,archived_at,action)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
            entry["id"], entry["version"], entry["kind"], entry["ticker"], entry["title"], entry["body"],
            "user", entry["created_at"], entry["updated_at"], entry["archived_at"], action))

    def get(self, entry_id):
        with self._connection() as conn:
            return self._get(conn, entry_id)

    def create(self, *, kind, ticker, title, body):
        kind, ticker, title, body = _content(kind, ticker, title, body)
        now = _now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute("""INSERT INTO journal_entries
                (kind,ticker,title,body,origin,created_at,updated_at,version)
                VALUES (?,?,?,?,'user',?,?,1)""", (kind, ticker, title, body, now, now))
            entry = self._get(conn, result.lastrowid)
            self._record(conn, entry, "create")
            return entry

    def update(self, entry_id, *, expected_version, kind, ticker, title, body):
        _positive(expected_version, _ui_text('Versione attesa', 'Expected version'))
        kind, ticker, title, body = _content(kind, ticker, title, body)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._get(conn, entry_id)
            if current["version"] != expected_version:
                raise JournalConflict(current["version"])
            if current["archived_at"]:
                raise JournalInvalid(_ui_text("Ripristina la nota dall'archivio prima di modificarla", 'Restore the archived note before editing it'))
            conn.execute("""UPDATE journal_entries SET kind=?,ticker=?,title=?,body=?,
                updated_at=?,version=version+1 WHERE id=? AND version=?""",
                (kind, ticker, title, body, _now(), entry_id, expected_version))
            entry = self._get(conn, entry_id)
            self._record(conn, entry, "update")
            return entry

    def set_archived(self, entry_id, *, expected_version, archived):
        _positive(expected_version, _ui_text('Versione attesa', 'Expected version'))
        if type(archived) is not bool:
            raise JournalInvalid(_ui_text('Stato archivio non valido', 'Invalid archive state'))
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._get(conn, entry_id)
            if current["version"] != expected_version:
                raise JournalConflict(current["version"])
            if bool(current["archived_at"]) == archived:
                return current
            now = _now()
            conn.execute("""UPDATE journal_entries SET archived_at=?,updated_at=?,version=version+1
                WHERE id=? AND version=?""", (now if archived else None, now, entry_id, expected_version))
            entry = self._get(conn, entry_id)
            self._record(conn, entry, "archive" if archived else "restore")
            return entry

    def list_entries(self, *, status="active", kind=None, ticker=None, query="", limit=50, offset=0):
        _page(limit, offset)
        if status not in ("active", "archived", "all") or kind not in (None, "thesis", "macro"):
            raise JournalInvalid(_ui_text('Filtro non valido', 'Invalid filter'))
        _text(query, _ui_text('Ricerca', 'Search'), 200, required=False)
        where, params = [], []
        if status != "all":
            where.append("archived_at IS " + ("NULL" if status == "active" else "NOT NULL"))
        if kind:
            where.append("kind=?")
            params.append(kind)
        if ticker:
            where.append("ticker=?")
            params.append(_ticker(ticker))
        if query.strip():
            escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\' OR ticker LIKE ? ESCAPE '\\')")
            params.extend(["%" + escaped + "%"] * 3)
        clause = " WHERE " + " AND ".join(where) if where else ""
        with self._connection() as conn:
            conn.execute("BEGIN")
            total = conn.execute("SELECT COUNT(*) FROM journal_entries" + clause, params).fetchone()[0]
            rows = conn.execute("SELECT " + _FIELDS + " FROM journal_entries" + clause +
                                " ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
            entries = []
            for row in rows:
                item = dict(row)
                item["excerpt"] = item.pop("body")[:180]
                entries.append(item)
            return {"items": entries, "total": total, "limit": limit, "offset": offset}

    def history(self, entry_id, *, limit=50, offset=0):
        _page(limit, offset)
        with self._connection() as conn:
            conn.execute("BEGIN")
            self._get(conn, entry_id)
            total = conn.execute("SELECT COUNT(*) FROM journal_revisions WHERE entry_id=?", (entry_id,)).fetchone()[0]
            rows = conn.execute("SELECT * FROM journal_revisions WHERE entry_id=? ORDER BY version DESC LIMIT ? OFFSET ?",
                                (entry_id, limit, offset)).fetchall()
            return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}
