# Packaging Lockly

These files are starting points for distribution packages. They have not been
submitted to any distribution, and they were not test-built as part of this
release, so expect to adjust them.

| Distribution | Files | Build |
| --- | --- | --- |
| Arch Linux | `arch/PKGBUILD` | `makepkg -si` next to a `lockly-0.1.0.tar.gz` source tarball |
| Fedora | `fedora/lockly.spec` | `rpmbuild -ba lockly.spec` (or `fedpkg local`) |
| Debian / Ubuntu | `debian/` | copy to `./debian` in the source tree, then `dpkg-buildpackage -us -uc` |

All packages install:

* the Python package and the `lockly` launcher script,
* `data/io.github.gerchanisko.Lockly.desktop` → `/usr/share/applications/`,
* `data/io.github.gerchanisko.Lockly.metainfo.xml` → `/usr/share/metainfo/`,
* the app icons → `/usr/share/icons/hicolor/{scalable,symbolic}/apps/`.

The GUI smoke test (`tests/test_ui_smoke.py`) needs `gtk4-broadwayd` and is
excluded from package builds.

Required runtime versions: Python ≥ 3.11, GTK ≥ 4.14, libadwaita ≥ 1.6,
PyGObject ≥ 3.48, PyNaCl ≥ 1.5. This rules out Debian 12 and Ubuntu 24.04
(libadwaita 1.2 / 1.5); Debian 13, Ubuntu 24.10+, Fedora 41+ and current Arch
work.
