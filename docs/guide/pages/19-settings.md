# F19 — Settings

[Handbook](../README.md) · [Previous: F18](./18-mandate-journal.md)

![F19 Settings: original synthetic DEMO mockup](../../assets/product/19-settings.svg)

*DEMO illustration: invented instruments, values and text; not a screenshot.*

**F19** opens the settings panel. It is the last navigation destination and is
also reachable through **Ctrl+K → F19**. It is a panel over the current workspace,
not a separate research route.

## Use it
1. Inspect backend health and version when diagnosing a connection problem.
2. Read the displayed model configuration to understand which configured roles
   the backend reports. Availability still depends on your provider account.
3. Review task or scheduler status and any errors. A disabled task is distinct
   from a failed or unknown task.
4. Inspect the backup calendar/list and create a database backup when needed.
5. Close the panel with its close control or Escape to return to your page.

## Choose the language
Select **Italiano** or **English**, save, and wait for the stored preference to be
read back. The choice applies to the interface and newly started research;
existing documents remain in their original language, and a run already started
keeps its captured language. Changing the preference does not start a committee
run, generate a briefing or place a transaction.

If saving has an uncertain outcome, use **Reload profile** to inspect the stored
preference before trying again. An unreadable preference is declared; the repair
action preserves a verified copy rather than silently defaulting to a language.

## Backup controls
A successful SQLite backup is useful evidence for that database. It does not
prove that every private JSON store, attachment, report, research index or
credential file was backed up too. Follow the broader
[update and privacy guide](../updates.md).

Deleting a backup is a destructive action and has a confirmation step. Keep a
known-good copy before deleting older ones. This panel is not a general restore
wizard; plan a restore with writers stopped and a copy of the current state.

Task result codes and returned backup files are separate observations. Files
dated on the same day do not by themselves establish which task created them or
why a task failed. A folder path is only shown when supplied by the backend;
missing paths and unreadable lists remain unavailable. The returned list can be
limited, so the calendar is not proof of complete historical backup coverage.

## What is configured elsewhere
Edit credentials and environment settings in your private .env file, then
restart the relevant process. The panel is not a secret-key editor or a model
marketplace. It does not make Windows scheduler installation portable to other
operating systems.

Reported health means the checked component answered. It is not proof that every
market provider, AI model and historical dataset is available. Use the
[troubleshooting guide](../troubleshooting.md) to narrow a failure to its source.
