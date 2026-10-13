# Lockly security model

This document describes what Lockly protects, how, and — just as importantly —
what it does not protect. It reflects the code in this repository (vault
format 1, Lockly 0.1.0). **Lockly has not been independently audited.**

## Reporting a vulnerability

Please report security problems privately rather than in a public issue.
Use the repository host's private vulnerability reporting feature (for
example GitHub's "Report a vulnerability" under the Security tab), or
contact the maintainers directly. Include the Lockly version, your
distribution, and steps to reproduce. Never include a real vault or real
credentials in a report — create a throw-away vault instead.

We aim to acknowledge reports within 7 days and to publish a fix and an
advisory once a fix is available.

## Threat model

Lockly is a **local, single-user** password manager.

It is designed to protect the vault against:

* someone who obtains a copy of the vault file (stolen laptop without disk
  encryption, a backup that leaks, a synced folder) but not the master
  password;
* accidental or malicious modification of the vault file — tampering is
  detected and the vault refuses to open rather than returning altered data;
* casual exposure on screen (passwords are hidden until revealed);
* lingering secrets on the clipboard (automatic clearing);
* an unattended, unlocked session (auto-lock on inactivity, on screen lock
  and before suspend).

It does **not** protect against:

* malware or another program running as your user while the vault is
  unlocked (it can read process memory, the clipboard, or record keystrokes);
* an attacker with root access, or physical access to a running, unlocked
  machine;
* a weak or reused master password — Argon2id slows guessing down, it cannot
  make a guessable password safe;
* an attacker who can modify the Lockly program or its Python dependencies;
* rollback: someone with write access to the file can replace the vault with
  an older copy of itself, or delete individual entries (see "Integrity"
  below).

## Cryptography

All primitives come from [libsodium](https://doc.libsodium.org/) via
[PyNaCl](https://pynacl.readthedocs.io/). Lockly does not implement any
cipher, MAC or KDF itself.

| Purpose | Primitive |
| --- | --- |
| Password → key | Argon2id v1.3 (`crypto_pwhash`), 16-byte random salt |
| Encryption | XChaCha20-Poly1305 IETF AEAD, 192-bit random nonce per message |
| Randomness | libsodium `randombytes_buf` (vault), Python `secrets` (generator) |
| Padding | `sodium_pad` (ISO/IEC 7816-4) to 256-byte blocks |

### Key hierarchy

```
master password ──Argon2id(salt, opslimit, memlimit)──▶ KEK (256-bit)
KEK  ──XChaCha20-Poly1305──▶ wraps the vault key (random 256-bit)
vault key ──XChaCha20-Poly1305──▶ each entry
```

* The **vault key** is generated once from the CSPRNG when the vault is
  created. Changing the master password re-wraps this key; entries are not
  re-encrypted.
* A wrong master password yields a different KEK, so unwrapping fails
  Poly1305 authentication and the password is rejected. There is no separate
  password hash or "verifier" stored.
* The key-wrap associated data binds the vault id, the KDF parameters and the
  salt. Changing any of them in the file makes unlocking fail instead of
  silently deriving a weaker key.

### Argon2id parameters

New vaults use libsodium's `MODERATE` profile: **3 passes, 256 MiB**. In
Settings → Security → Change Master Password you can switch to the
`SENSITIVE` profile (4 passes, 1 GiB). Parameters are stored in the vault
header. When reading a vault, Lockly rejects parameters outside sane bounds
(memory above 4 GiB, iterations above 20, anything below libsodium's
minimums) so a crafted file cannot exhaust memory.

### Entries

Each entry is serialised to JSON, padded, and encrypted on its own with the
vault key. The associated data is `"lockly/entry" ‖ vault id ‖ entry id`,
which means a ciphertext cannot be moved to another row or into another vault
without detection.

## What is stored where

### The vault file (`~/.local/share/lockly/vault.lockly`)

A SQLite database, mode `0600`, in a directory with mode `0700`.

**Encrypted** (inside each entry ciphertext): title, entry type, username,
password, website, notes, note content, identity fields, card holder, card
number, expiry date, security code, custom fields, tags, favourite flag,
created / modified / deleted timestamps.

**Not encrypted** (visible to anyone with the file):

* the vault format version, a random vault id, the Argon2id parameters, the
  salt, and the wrapped vault key;
* the number of entries (including entries in the Trash);
* a random 128-bit id per entry;
* each entry's ciphertext length, rounded up to a multiple of 256 bytes;
* file-system metadata such as the file's modification time.

SQLite runs with `secure_delete=ON`, so deleted rows are overwritten rather
than left in free pages. This does not defeat copy-on-write file systems,
SSD wear levelling, or snapshots/backups taken by the system.

### Settings (`~/.config/lockly/settings.json`)

Plain JSON with appearance, auto-lock timeout, clipboard timeout, sort order
and password generator defaults. It never contains keys, passwords or entry
data.

### Logs

Lockly logs only event names (e.g. "Vault unlocked") and error types. It never
logs passwords, entry contents or key material. Set `LOCKLY_DEBUG=1` for
debug logs; the same rule applies.

## Integrity

Every entry and the wrapped vault key are authenticated. Bit flips, truncated
values, swapped ciphertexts and modified header values are detected and the
vault refuses to open with an integrity error.

Lockly does **not** authenticate the *set* of entries as a whole. An attacker
who can write to the file can delete individual rows or replace the file with
an older version, and Lockly cannot tell. Detecting this would require a
signed, versioned manifest; it is on the roadmap.

## Memory

While the vault is unlocked, decrypted entries and the vault key are held in
process memory.

* The vault key and the KEK are kept in `bytearray`s and overwritten with
  zeros when no longer needed. Python and libsodium bindings make transient
  `bytes` copies that cannot be wiped.
* Decrypted entries are Python strings, which are immutable and cannot be
  reliably wiped. On lock Lockly drops all references, clears the in-memory
  search index, removes every decrypted value from the widget tree and runs
  the garbage collector, but the memory may not be overwritten immediately.
* Core dumps are disabled at start-up and the process marks itself
  non-dumpable (`PR_SET_DUMPABLE=0`), which also blocks same-user `ptrace`
  attachment on most kernels. Swap is not controlled by Lockly — use
  encrypted swap or none.

## Clipboard

Copied passwords, card numbers and hidden fields are cleared after a
configurable delay (30 s by default). Lockly only clears the clipboard if it
still holds the exact content Lockly placed there; if another application has
replaced it, it is left alone. If clearing fails Lockly says so instead of
claiming success. Copied secrets carry the `x-kde-passwordManagerHint` MIME
type, which tells clipboard managers that honour it (e.g. KDE Klipper) not to
store them.

Limitations: while a secret is on the clipboard any application can read it.
Clipboard history tools that ignore the hint may keep a copy. On Wayland the
compositor decides whether a background application may change the
selection; Lockly reports what GTK tells it, which may not reflect a
compositor that silently ignores the request. The clipboard is also cleared
when the vault locks and when Lockly quits.

## Locking

* The vault is locked at every start-up; unlocked state is never persisted.
* Manual lock (Ctrl+L), inactivity timeout (default 5 minutes), screen lock,
  session lock and suspend (via `org.freedesktop.ScreenSaver`,
  `org.gnome.ScreenSaver` and `org.freedesktop.login1` signals on D-Bus).
* Locking closes all dialogs, cancels open file choosers, clears the
  clipboard if it holds a Lockly secret, empties the UI and the search index,
  wipes the vault key and discards decrypted entries. Background operations
  that finish after a lock discard their results.
* Re-authentication with the master password is required for: changing the
  master password, exporting an encrypted backup, and exporting a plaintext
  CSV.

## Backups and exports

**Encrypted backup** (`.lockly-backup`): a JSON document containing all
entries, encrypted with XChaCha20-Poly1305 under a key derived with Argon2id
(256 MiB) from a backup password chosen at export time. The format name,
version, KDF parameters and salt are bound as associated data. Files are
written atomically (temporary file, `fsync`, rename) with mode `0600`.
Imports are fully decrypted and validated before anything is written, then
merged in a single transaction; nothing is deleted by an import.

**CSV export** writes every secret in plain text. It requires an explicit
confirmation and the master password. Values starting with `=`, `+`, `-`,
`@` are prefixed with `'` to prevent spreadsheet formula injection. Trash
items are not exported.

Lockly does not claim compatibility with other password managers' formats.

## What Lockly does not claim

* No end-to-end encrypted sync — there is no sync at all.
* No "zero-knowledge cloud" — there is no cloud.
* No independent security audit.
* No protection against a compromised desktop session.

## Other hardening

* Lockly makes no network connections. Service icons are drawn locally from
  the entry title; favicons are never downloaded.
* Website links are opened only for `http`/`https` URLs, through the desktop's
  URI handler. No vault content is ever executed or passed to a shell.
* Creating a vault never overwrites an existing file (`os.link` into place).
* Vault format migrations run only after the master password is verified,
  inside one transaction, after a copy of the original file is saved next to
  it.
