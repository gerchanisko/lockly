# Contributing to Lockly

Thanks for helping. Lockly handles people's most sensitive data, so the bar
for changes is "obviously correct" rather than "probably fine".

## Ground rules

* **No custom cryptography.** Use libsodium primitives through PyNaCl. Any
  change to the vault or backup format needs a format version bump, a
  migration in `core/migrations.py`, tests, and an update to `docs/SECURITY.md`.
* **Never log secrets.** Not passwords, not entry contents, not key material,
  not even at debug level. Log event names and exception *types*.
* **Keep `lockly.core` free of GTK.** Everything security-relevant must be
  testable without a display.
* **No network access** without a design discussion first.
* **Never use real credentials** in tests, screenshots, bug reports or sample
  data. Use obviously fake values and `example.com` / `example.org` domains.
* **Don't claim what isn't true.** UI text and docs must match what the code
  does — e.g. never say the clipboard was cleared unless it verifiably was.

## Development setup

```sh
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
LOCKLY_VAULT=/tmp/dev.lockly .venv/bin/python -m lockly
```

See the README for distribution packages you need first.

## Pull requests

* One logical change per PR, with tests. Bug fixes should come with a test
  that fails without the fix.
* `pytest` must pass, including `tests/test_storage_security.py`.
* UI changes: follow the GNOME Human Interface Guidelines, keep keyboard
  navigation and accessible labels working, and attach before/after
  screenshots (`tools/screenshots.py` renders them from a throw-away vault).
* Code style: PEP 8, type hints on public functions, comments explain *why*.

## Reporting security issues

Please don't open public issues for vulnerabilities. See
[docs/SECURITY.md](docs/SECURITY.md#reporting-a-vulnerability).
