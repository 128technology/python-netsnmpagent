#
# spec file for package python-netsnmpagent
#
# Copyright (c) 2013 Pieter Hollants <pieter@hollants.com>
#

Name:           python-netsnmpagent
Version:        %{netsnmpagent_version}
Release:        0
License:        GPL-3.0
Summary:        Facilitates writing Net-SNMP (AgentX) subagents in Python
Url:            http://pypi.python.org/pypi/netsnmpagent
Group:          Development/Languages/Python
Source:         http://pypi.python.org/packages/source/n/netsnmpagent/netsnmpagent-%{version}.tar.gz
BuildRequires:  python-devel
BuildRoot:      %{_tmppath}/%{name}-%{version}-build
BuildArch:      noarch

%description
python-netsnmpagent is a Python module that facilitates writing Net-SNMP
subagents in Python. Subagents connect to a locally running Master agent
(snmpd) over a Unix domain socket (eg. "/var/run/agentx/master") and using the
AgentX protocol (RFC2747). They implement custom Management Information Base
(MIB) modules that extend the local node's MIB tree. Usually, this requires
writing a MIB as well, ie. a text file that specifies the structure, names
and data types of the information within the MIB module.

%prep
%setup -q -n netsnmpagent-%{version}

%build
CFLAGS="%{optflags}" python setup.py build

%install
python setup.py install --prefix=%{_prefix} --root=%{buildroot}

%files
%defattr(-,root,root,-)
%{python_sitelib}/*
%doc README LICENSE ChangeLog
%doc EXAMPLE-MIB.txt example_agent.py run_example_agent.sh

%changelog