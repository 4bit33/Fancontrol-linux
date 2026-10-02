Name:           fancontrol-linux
Version:        1.1.1
Release:        1%{?dist}
Summary:        Fan control in the shape of FanControl for Windows

License:        GPL-3.0-or-later
URL:            https://github.com/4bit33/Fancontrol-linux
# Built from a tag by .copr/Makefile, which names the archive the same way.
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  systemd-rpm-macros
BuildRequires:  desktop-file-utils
# For the tests in %%check.
BuildRequires:  python3-pytest
BuildRequires:  python3-pyside6
BuildRequires:  python3-dasbus
BuildRequires:  python3-gobject

Requires:       python3-dasbus
Requires:       python3-gobject
Requires:       python3-pyside6
%{?systemd_requires}

%description
A daemon that drives the PWM fan outputs Linux exposes through hwmon, and
NVIDIA graphics card fans through NVML, with a Qt window in the style of
FanControl for Windows. Curves of seven kinds, hysteresis and smoothing
separately for rising and falling temperatures, calibration, and import of
FanControl's userConfig.json.

After installing: sudo systemctl enable --now fancontrold, then open
Fan Control from the menu.


%package nvidia
Summary:        Let fancontrol-linux drive NVIDIA graphics card fans
Requires:       %{name} = %{version}-%{release}
# Pulled in on its own wherever the NVIDIA driver's NVML is installed,
# whichever repository the driver came from.
Supplements:    (%{name} and libnvidia-ml.so.1()(64bit))

%description nvidia
The daemon runs without any capabilities, which is all board fans need. The
NVIDIA driver refuses to set fan speeds for a process that holds none, even
as root, so this drop-in gives the daemon its capabilities back. Installed
automatically when the NVIDIA driver is.


%prep
%autosetup -n Fancontrol-linux-%{version}


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files -l fancontrol

# The unit in the tree points at install.sh's own environment; a package uses
# the system interpreter. Starting the interpreter rather than the console
# script matters under SELinux - see the comment in the unit.
sed 's|^ExecStart=.*|ExecStart=%{python3} -m fancontrol.daemon|' \
    data/systemd/fancontrold.service > fancontrold.service
install -Dpm644 fancontrold.service %{buildroot}%{_unitdir}/fancontrold.service
install -Dpm644 data/systemd/fancontrold-nvidia.conf \
    %{buildroot}%{_unitdir}/fancontrold.service.d/nvidia.conf

# Fedora's administrators are the wheel group; drop the Debian and Ubuntu
# blocks, as install.sh does for groups a machine does not have.
python3 - data/dbus/org.fancontrol.Daemon.conf org.fancontrol.Daemon.conf <<'EOF'
import re, sys
text = open(sys.argv[1]).read()
keep = lambda m: m.group(0) if m.group(1) == "wheel" else ""
open(sys.argv[2], "w").write(
    re.sub(r'  <policy group="([^"]+)">.*?</policy>\n?', keep, text, flags=re.S))
EOF
install -Dpm644 org.fancontrol.Daemon.conf \
    %{buildroot}%{_datadir}/dbus-1/system.d/org.fancontrol.Daemon.conf

desktop-file-install --dir=%{buildroot}%{_datadir}/applications \
    data/applications/io.github.fancontrol_linux.gui.desktop

# Tells the window's update notice to point at dnf rather than git.
install -d %{buildroot}%{_datadir}/%{name}
echo rpm > %{buildroot}%{_datadir}/%{name}/installed-by

install -d %{buildroot}%{_sysconfdir}/%{name}


%check
export QT_QPA_PLATFORM=offscreen
%pytest -q


%post
%systemd_post fancontrold.service

%preun
%systemd_preun fancontrold.service

%postun
# Restarting on upgrade is what makes an update take effect.
%systemd_postun_with_restart fancontrold.service

%post nvidia
systemctl daemon-reload >/dev/null 2>&1 || :
systemctl try-restart fancontrold.service >/dev/null 2>&1 || :

%postun nvidia
systemctl daemon-reload >/dev/null 2>&1 || :
systemctl try-restart fancontrold.service >/dev/null 2>&1 || :


%files -f %{pyproject_files}
%doc README.md README.uk.md CHANGELOG.md
%{_bindir}/fancontrold
%{_bindir}/fanctl
%{_bindir}/fancontrol-gui
%{_bindir}/fancontrol-sim
%{_unitdir}/fancontrold.service
%{_datadir}/dbus-1/system.d/org.fancontrol.Daemon.conf
%{_datadir}/applications/io.github.fancontrol_linux.gui.desktop
%{_datadir}/%{name}/
# The daemon writes config.json here; ProtectSystem=strict needs it to exist.
%dir %{_sysconfdir}/%{name}

%files nvidia
%dir %{_unitdir}/fancontrold.service.d
%{_unitdir}/fancontrold.service.d/nvidia.conf


%changelog
* Thu Sep 24 2026 4bit33 <luisadgg5@gmail.com> - 1.1.1-1
- First release as a Fedora package

* Thu Sep 24 2026 4bit33 <luisadgg5@gmail.com> - 1.1.0-1
- Package for Fedora
