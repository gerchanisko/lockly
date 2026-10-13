Name:           lockly
Version:        0.1.0
Release:        1%{?dist}
Summary:        Local-first password manager for the Linux desktop
License:        GPL-3.0-or-later
URL:            https://github.com/gerchanisko/lockly
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  python3-pytest
BuildRequires:  python3-pynacl
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita >= 1.6
Requires:       python3-pynacl

%description
Lockly keeps logins, secure notes, identities and payment cards in a vault
encrypted with Argon2id and XChaCha20-Poly1305 (libsodium). It works offline
and never syncs or uploads anything.

%prep
%autosetup

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files lockly
install -Dm644 data/io.github.gerchanisko.Lockly.desktop %{buildroot}%{_datadir}/applications/io.github.gerchanisko.Lockly.desktop
install -Dm644 data/io.github.gerchanisko.Lockly.metainfo.xml %{buildroot}%{_metainfodir}/io.github.gerchanisko.Lockly.metainfo.xml
install -Dm644 src/lockly/resources/icons/hicolor/scalable/apps/io.github.gerchanisko.Lockly.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/io.github.gerchanisko.Lockly.svg
install -Dm644 src/lockly/resources/icons/hicolor/symbolic/apps/io.github.gerchanisko.Lockly-symbolic.svg %{buildroot}%{_datadir}/icons/hicolor/symbolic/apps/io.github.gerchanisko.Lockly-symbolic.svg

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/io.github.gerchanisko.Lockly.desktop
%pytest --ignore=tests/test_ui_smoke.py

%files -f %{pyproject_files}
%license LICENSE
%doc README.md docs/SECURITY.md
%{_bindir}/lockly
%{_datadir}/applications/io.github.gerchanisko.Lockly.desktop
%{_metainfodir}/io.github.gerchanisko.Lockly.metainfo.xml
%{_datadir}/icons/hicolor/*/apps/io.github.gerchanisko.Lockly*.svg

%changelog
* Tue Oct 13 2026 Lockly contributors - 0.1.0-1
- First release
