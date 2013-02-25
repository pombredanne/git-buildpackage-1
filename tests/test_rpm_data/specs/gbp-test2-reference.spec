Name:       gbp-test2
Summary:    Test package 2 for git-buildpackage
Epoch:      2
Version:    3.0
Release:    0
Group:      Development/Libraries
License:    GPLv2
Source10:   ftp://ftp.host.com/%{name}-%{version}.tar.gz
Source:     foo.txt
Source20:   bar.tar.gz
# Gbp-Ignore-Patches: -1
Patch:      my.patch
# Patches auto-generated by git-buildpackage:
Patch0:     new.patch
Packager:   Markus Lehtonen <markus.lehtonen@linux.intel.com>

%description
Package for testing the RPM functionality of git-buildpackage.


%prep
%setup -T -n %{name}-%{version} -c -a 10

%patch

echo "Do things"

# Gbp-Patch-Macros
# new.patch
%if 1
%patch0 -p1
%endif

%build
make


%install
rm -rf %{buildroot}
mkdir -p %{buildroot}/%{_datadir}/%{name}
cp -R * %{buildroot}/%{_datadir}/%{name}
install %{SOURCE0} %{buildroot}/%{_datadir}/%{name}



%files
%defattr(-,root,root,-)
%dir %{_datadir}/%{name}
%{_datadir}/%{name}
