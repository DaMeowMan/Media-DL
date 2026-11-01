# Fedora / RHEL-family / openSUSE spec. Pure-Python app, venv built in %post.
%global debug_package %{nil}
%global _python_bytecompile_extra 0

Name:           media-dl
Version:        @VERSION@
Release:        1%{?dist}
Summary:        Tk front-end for yt-dlp and gallery-dl
License:        @LICENSE@
URL:            @URL@
Source0:        media-dl-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3
Requires:       curl
Requires:       ca-certificates
Requires:       mpv
Requires:       aria2
Requires:       hicolor-icon-theme
# file deps: satisfied by ffmpeg-free (Fedora) or full ffmpeg (RPM Fusion / Packman)
Requires:       /usr/bin/ffmpeg
Requires:       /usr/bin/ffprobe
Requires:       /usr/bin/AtomicParsley
%if 0%{?suse_version}
Requires:       python3-tk
Requires:       python3-Pillow
Requires:       python3-Pillow-tk
Requires:       libmpv2
%else
Requires:       python3-tkinter
Requires:       python3-pip
Requires:       python3-pillow
Requires:       python3-pillow-tk
Requires:       mpv-libs
%endif
Recommends:     uv
Recommends:     nodejs
Recommends:     rtmpdump

%description
Tk GUI that downloads videos with yt-dlp and images with gallery-dl, with an
embedded mpv player, image/thumbnail preview and a yt-dlp extractor picker.
yt-dlp, gallery-dl, python-mpv, curl_cffi (impersonation), mutagen and the
other optional yt-dlp dependencies are installed into a virtualenv at
/opt/media-dl/venv with uv when the package is installed.

%prep
%autosetup -n media-dl-%{version}

%build
# nothing to build

%install
mkdir -p %{buildroot}
cp -a usr %{buildroot}/

%post
/usr/lib/media-dl/media-dl-setup
update-desktop-database -q &>/dev/null || :
gtk-update-icon-cache -q -t -f %{_datadir}/icons/hicolor &>/dev/null || :

%preun
# $1 == 0 -> final removal (not an upgrade)
if [ "$1" -eq 0 ]; then
    /usr/lib/media-dl/media-dl-setup --purge || :
fi

%postun
if [ "$1" -eq 0 ]; then
    update-desktop-database -q &>/dev/null || :
    gtk-update-icon-cache -q -t -f %{_datadir}/icons/hicolor &>/dev/null || :
fi

%files
%{_prefix}/lib/media-dl/
%{_bindir}/media-dl
%{_bindir}/media-dl-update
%{_datadir}/applications/media-dl.desktop
%{_datadir}/icons/hicolor/*/apps/media-dl.png
%ghost %dir /opt/media-dl

%changelog
* Sat Oct 31 2026 @MAINTAINER@ - @VERSION@-1
- Initial package
