"""Entry point."""

from __future__ import annotations

import logging
import os
import sys


def harden_process() -> None:
    """Disable core dumps and same-user ptrace attachment where possible.

    Core dumps could contain decrypted vault data. PR_SET_DUMPABLE=0 also
    stops other unprivileged processes of the same user from attaching a
    debugger (subject to the kernel's Yama settings). Both are best effort.
    """
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, ValueError, OSError):
        pass
    if sys.platform.startswith("linux") and not os.environ.get("LOCKLY_ALLOW_DEBUG"):
        try:
            import ctypes

            libc = ctypes.CDLL(None, use_errno=True)
            PR_SET_DUMPABLE = 4
            libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
        except (OSError, AttributeError):
            pass


def main(argv: list[str] | None = None) -> int:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")

    # Logs never contain secrets, but keep them quiet unless asked for.
    level = logging.DEBUG if os.environ.get("LOCKLY_DEBUG") else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")

    from gi.repository import Adw

    if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 6):
        sys.stderr.write("Lockly requires libadwaita 1.6 or newer.\n")
        return 1

    harden_process()

    from .application import LocklyApplication

    app = LocklyApplication()
    return app.run(sys.argv if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
