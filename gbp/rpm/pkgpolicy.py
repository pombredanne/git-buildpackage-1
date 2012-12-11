# vim: set fileencoding=utf-8 :
#
# (C) 2012 Intel Corporation <markus.lehtonen@linux.intel.com>
#    This program is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; either version 2 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program; if not, write to the Free Software
#    Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
"""Default packaging policy for RPM"""

import re
import gbp.log
from gbp.pkg import PkgPolicy, parse_archive_filename

# When trying to parse a version-number, these are
# the valid characters.
rpm_version_chars = 'a-zA-Z\d.~+'

class RpmPkgPolicy(PkgPolicy):
    """Packaging policy for RPM"""

    # Special rpmlib python module for GBP (only)
    python_rpmlib_module_name = "rpmlibgbp"

    # "Do NOT use a plus '+', or a period '.' as a delimiter"
    # Additionally, name must begin with an alphanumeric
    packagename_re = re.compile("^[a-zA-Z0-9][a-zA-Z0-9\-_]+$")
    packagename_msg = """Package names must be at least two characters long, start with an
    alphanumeric and can only contain alphanumerics or minus signs (-)"""

    # The upstream_version may contain only alphanumerics and the characters
    # . ~ _ (full stop, tilde, underscores) and# should start with a digit.
    # "We can use letters and tilde into the version tag. We do not use the
    # Release field for this."
    upstreamversion_re = re.compile("^[0-9][a-zA-Z0-9\.\~_]*$")
    upstreamversion_msg = """Upstream version numbers must start with a digit and can only containg alphanumerics,
    full stops (.),tildes (~) and underscores (_)"""

    # Time stamp format to be used in tagging
    tag_timestamp_format = "%Y%m%d"

    @classmethod
    def is_valid_orig_archive(cls, filename):
        """
        Is this a valid orig source archive

        @param filename: upstream source archive filename
        @type filename: C{str}
        @return: true if valid upstream source archive filename
        @rtype: C{bool}

        >>> RpmPkgPolicy.is_valid_orig_archive("foo/bar_baz.tar.gz")
        True
        >>> RpmPkgPolicy.is_valid_orig_archive("foo.bar.tar")
        True
        >>> RpmPkgPolicy.is_valid_orig_archive("foo.bar")
        False
        >>> RpmPkgPolicy.is_valid_orig_archive("foo.gz")
        False
        """
        (base, arch_fmt, compression) = parse_archive_filename(filename)
        if arch_fmt:
            return True
        return False

    @classmethod
    def split_full_version(cls, version):
        """
        Parse full version string and split it into individual "version
        components", i.e. upstreamversion, epoch and release

        @param version: full version of a package
        @type version: C{str}
        @return: individual version components
        @rtype: C{dict}

        >>> RpmPkgPolicy.split_full_version("1")
        {'release': None, 'epoch': None, 'upstreamversion': '1'}
        >>> RpmPkgPolicy.split_full_version("1.2.3-5.3")
        {'release': '5.3', 'epoch': None, 'upstreamversion': '1.2.3'}
        >>> RpmPkgPolicy.split_full_version("3:1.2.3")
        {'release': None, 'epoch': '3', 'upstreamversion': '1.2.3'}
        >>> RpmPkgPolicy.split_full_version("3:1-0")
        {'release': '0', 'epoch': '3', 'upstreamversion': '1'}
        """
        epoch = None
        upstreamversion = None
        release = None

        e_vr = version.split(":", 1)
        if len(e_vr) == 1:
            v_r = e_vr[0].split("-", 1)
        else:
            epoch = e_vr[0]
            v_r = e_vr[1].split("-", 1)
        upstreamversion = v_r[0]
        if len(v_r) > 1:
            release = v_r[1]

        return {'epoch': epoch, 'upstreamversion': upstreamversion, 'release': release}

    @classmethod
    def compose_full_version(cls, evr = {}):
        """
        Compose a full version string from individual "version components",
        i.e. epoch, version and release

        @param evr: dict of version components
        @type evr: C{dict} of C{str}
        @return: full version
        @rtype: C{str}

        >>> RpmPkgPolicy.compose_full_version({'epoch': '', 'upstreamversion': '1.0'})
        '1.0'
        >>> RpmPkgPolicy.compose_full_version({'epoch': '2', 'upstreamversion': '1.0', 'release': None})
        '2:1.0'
        >>> RpmPkgPolicy.compose_full_version({'epoch': None, 'upstreamversion': '1', 'release': '0'})
        '1-0'
        >>> RpmPkgPolicy.compose_full_version({'epoch': '2', 'upstreamversion': '1.0', 'release': '2.3'})
        '2:1.0-2.3'
        >>> RpmPkgPolicy.compose_full_version({'epoch': '2', 'upstreamversion': '', 'release': '2.3'})
        """
        if 'upstreamversion' in evr and evr['upstreamversion']:
            version = ""
            if 'epoch' in evr and evr['epoch']:
                version += "%s:" % evr['epoch']
            version += evr['upstreamversion']
            if 'release' in evr and evr['release']:
                version += "-%s" % evr['release']
            if version:
                return version
        return None



