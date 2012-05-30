Name:       git-buildpackage
Summary:    Build packages from git
Version:    0.6.0git20120419
Release:    0
Group:      Development/Tools/Building
License:    GPLv2
BuildArch:  noarch
URL:        https://honk.sigxcpu.org/piki/projects/git-buildpackage/
Source0:    %{name}_%{version}.tar.gz
# >> gbp-patch-tags         # auto-added by gbp
# << gbp-patch-tags         # auto-added by gbp
Requires:   %{name}-common = %{version}-%{release}
Requires:   dpkg
BuildRequires:  python
BuildRequires:  python-setuptools
BuildRequires:  docbook-utils
BuildRequires:  gtk-doc
BuildRequires:  epydoc
BuildRequires:  python-coverage
BuildRequires:  python-nose

%description
Set of tools from Debian that integrate the package build system with Git.
This package contains the original Debian tools.


%package common
Summary:    Common files for git-buildpackage debian and rpm tools
Group:      Development/Tools/Building
Requires:   git-core
Requires:   python-base

%description common
Common files and documentation, used by both git-buildpackage debian and rpm tools


%package rpm
Summary:    Build RPM packages from git
Group:      Development/Tools/Building
Requires:   %{name}-common = %{version}-%{release}
Requires:   rpm
Requires:   rpm-python


%description rpm
Set of tools from Debian that integrate the package build system with Git.
This package contains the tools for building RPM packages.



%prep
%setup -q -n %{name}-%{version}

# >> gbp-apply-patches    # auto-added by gbp
# << gbp-apply-patches    # auto-added by gbp



%build
python ./setup.py build

# Prepare apidocs
epydoc -n git-buildpackage --no-sourcecode -o docs/apidocs/ \
gbp*.py git*.py gbp/

# HTML docs
HAVE_SGML2X=0 make -C docs/



%install
rm -rf %{buildroot}
python ./setup.py install --root=%{buildroot} --prefix=/usr
rm -rf %{buildroot}%{python_sitelib}/*info


%files
%defattr(-,root,root,-)
%{_bindir}/gbp-pq
%{_bindir}/git-buildpackage
%{_bindir}/git-dch
%{_bindir}/git-import-dsc
%{_bindir}/git-import-dscs
%{_bindir}/git-import-orig
%{_bindir}/git-pbuilder
%{_bindir}/gbp-create-remote-repo
%{python_sitelib}/gbp/deb/

%files common
%defattr(-,root,root,-)
%{_bindir}/gbp-clone
%{_bindir}/gbp-pull
%{python_sitelib}/gbp
%{python_sitelib}/gbp/*.py*
%{python_sitelib}/gbp/git/
%{python_sitelib}/gbp/pkg/
%{python_sitelib}/gbp/scripts/clone.py*
%{python_sitelib}/gbp/scripts/pull.py*
%config %{_sysconfdir}/git-buildpackage


%files rpm
%defattr(-,root,root,-)
%{_bindir}/gbp-pq-rpm
%{_bindir}/git-buildpackage-rpm
%{_bindir}/git-import-orig-rpm
%{_bindir}/git-import-srpm
%{python_sitelib}/gbp/rpm/
