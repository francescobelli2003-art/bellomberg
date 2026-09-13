# Update safely and publish code separately

[Handbook](README.md) · [Troubleshooting](troubleshooting.md)

## Updating your own installation

An application update should replace code and dependencies, not your portfolio.
Keep private files in their existing configured locations.

1. Read the [changelog](../../CHANGELOG.md) and record which version you are
   running: the number declared in `pyproject.toml` and `app/package.json`, and
   the commit of your checkout (`git rev-parse --short HEAD`).
2. Create a database backup using **F19 Settings**. Keep a separate private backup
   of configuration, mandate, research files and instrument metadata.
3. Close the desktop and stop the backend and any scheduled writers before
   replacing files or migrating a live database. A frontend window being closed
   does not prove an independently started backend has stopped.
4. From the source checkout, inspect local changes and fetch the repository:
   `git status --short`, then `git fetch origin`.
5. If you have a clean public-source checkout and the release follows the branch
   you track, `git pull --ff-only` applies a fast-forward update. Stop if Git
   reports local changes or a divergence. Preserve your work; do not use reset,
   clean or a force update to make the warning disappear.
6. Reinstall dependencies from the updated package and lockfile:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -e . pytest
   cd app
   npm ci
   npm run desktop:install
   cd ..
   ```

7. Start with the same backend directory, interpreter and runtime paths as before.
   Schema migrations are versioned; journal and progress tables are additive.
   Read startup errors before continuing.
8. Sign in, compare positions and cash with your pre-update records, open a saved
   memo, check the mandate and inspect new pages. A new empty book usually means
   the process is using another data directory—do not enter replacement trades.

Choose and save the interface language if this profile has not selected one yet.
Existing memos and specialist reports keep their original text; records without
language metadata remain unknown. Changing the preference does not translate or
regenerate that history. Preserve `preferences.json` with your private profile.

Existing holdings are not automatically converted into documented opening
positions. The new F16 opening-position workflow is an explicit balance entry
with provenance; it does not infer past trades, historical FX or returns.

Do not overwrite `.env` from the example during an update. Compare newly documented
variables and add only the choices you need. Your existing chat output limit,
models and provider keys are not replaced automatically.

## Legacy cash migration

Only an old installation whose cash still lives in its legacy JSON input needs
the explicit migration tool. New installations and installations with initialized
SQLite cash do not need it.

At the checkout root, with operational writers stopped, preview first:

```powershell
.\.venv\Scripts\python.exe tools/migrations/migra_cassa_sqlite.py
```

Read its reported source, destination and checks. Apply only the intended,
uninitialized migration:

```powershell
.\.venv\Scripts\python.exe tools/migrations/migra_cassa_sqlite.py --apply
```

The apply path creates verified backups and refuses to overwrite initialized
cash. Do not rerun an already completed migration to “refresh” a balance. Never
guess a replacement cash number.

## What a backup protects

A consistent SQLite backup includes committed database state even when WAL is
used. Copying just the live `.db` file while writers are active is not a reliable
backup procedure. The Settings backup should not be mistaken for a complete
backup of `.env`, model files, research attachments, private JSON stores and the
semantic index. Preserve those separately with writers stopped and verify what
your backup contains.

The UI can create and remove database backups. Deletion is permanent and requires
its own confirmation. The UI is not a universal “undo this release” button:
rolling back code and rolling back a schema/data migration are different tasks.
Use the release's specific recovery procedure and preserve changes made after
the backup.

## Contributing without publishing your own data

Using Bellomberg does not automatically push your files. **Pushing an operational
checkout is nevertheless unsafe**: `.gitignore` does not remove data already
tracked in Git history, and private details can exist in examples, tests, notes,
screenshots, commit messages or old branches.

Use a separate clean development/release checkout. Work with synthetic data and
review the exact staged files and diff. Do not copy `data/`, `.env`, reports,
personal mandates, metadata stores, logs, browser storage or operational archives.
Do not reuse an operational repository's history for a new public distribution.

The maintainer's release process uses an allowlisted source export, privacy and
secret checks, a tested artifact and a clean destination history. Its certificate
is tied to the destination/commit/tree/history. A later change needs new checks;
an old certificate does not cover arbitrary future updates. Some release policy
and private scanning corpus are intentionally absent from public source, so
shipping the release tool's Python file alone is not a complete privacy setup.

Before your own public contribution, inspect all content and history you intend
to publish and run appropriate checks for your data. A successful grep or a clean
working tree alone does not establish privacy. Code publication also cannot
recall copies already downloaded from a previous public repository.
