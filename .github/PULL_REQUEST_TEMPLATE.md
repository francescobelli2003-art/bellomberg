<!--
Bellomberg is developed in a private repository and published through an
allowlisted export. Accepted changes are applied upstream and appear in a later
sync commit, so this pull request may be closed with a reference to that commit
instead of being merged directly. See CONTRIBUTING.md before filling this in.
-->

## What changes and why

<!-- The concrete trigger, the previous behavior, the resulting behavior.
     Link the issue or discussion if there is one. -->

## Tests run

<!-- Tick what you ran and paste the real result lines. Say which live
     integrations (providers, broker, email, model calls) remain untested. -->

- [ ] `python -m pytest tests/ -q` (offline suite)
- [ ] `cd app && npx tsc --noEmit`
- [ ] `cd app && npm run test:release`
- [ ] `cd app && npm run build:bundles`
- [ ] `python docs/guide/verify_docs.py` (documentation changes)
- [ ] A test written before the fix, which fails without it: `tests/...`

## Documentation

- [ ] Handbook, README or `docs/` updated where the behavior is visible to users
- [ ] `CHANGELOG.md` entry under `[Unreleased]`
- [ ] Not needed because: <!-- reason -->

## New files

<!-- The export allowlist is private. List every new file so the maintainer can
     add it; a file that is not listed does not reach the public repository. -->

- none

## Checklist

- [ ] No secrets, tokens, `.env` content or PIN anywhere in the change.
- [ ] No personal or portfolio data (positions, balances, mandates, journal text,
      absolute personal paths, real identifiers) in code, tests, fixtures,
      screenshots or this description; instruments and values are synthetic.
- [ ] Missing or stale data stays declared (`n.d.`, `STALE`, error); no silent
      default or proxy was introduced.
- [ ] The change is surgical; unrelated cleanup is in a separate pull request.
- [ ] Existing assertions were not weakened and no skip was added to hide a failure.
- [ ] User-facing strings follow the interface-text rule in CONTRIBUTING.md.
- [ ] Model identifiers, financial policies and the documented-record gate of the
      valuation engines are unchanged, or the change is explained above.
- [ ] New dependencies are justified above and avoid major-version jumps.
