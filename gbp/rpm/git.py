# vim: set fileencoding=utf-8 :
#
# (C) 2011 Guido Günther <agx@sigxcpu.org>
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
import re

from gbp.git import GitRepository, GitRepositoryError
from gbp.pkg.pristinetar import PristineTar
from gbp.rpm.policy import RpmPkgPolicy

class RpmGitRepository(GitRepository):
    """A git repository that holds the source of an RPM package"""

    def __init__(self, path):
        super(RpmGitRepository, self).__init__(path)
        self.pristine_tar = PristineTar(self)

    def find_version(self, format, version, vendor="vendor"):
        """
        Check if a certain version is stored in this repo and return the SHA1
        of the related commit. That is, an annotated tag is dereferenced to the
        commit object it points to.

        @param format: tag pattern
        @type format: C{str}
        @param version: rpm version components ('epoch', 'upstreamversion', 'release',...)
        @type version: C{dict} of C{str}
        @param vendor: distribution vendor
        @type vendor: C{str}
        @return: sha1 of the commit the tag references to
        """
        tag = self.version_to_tag(format, version, vendor)
        if self.has_tag(tag): # new tags are injective
            # dereference to a commit object
            return self.rev_parse("%s^0" % tag)
        return None

    @staticmethod
    def version_to_tag(format, version, vendor="vendor"):
        """
        Generate a tag from a given format and a version

        @param format: tag pattern
        @type format: C{str}
        @param version: rpm version components ('epoch', 'upstreamversion', 'release',...)
        @type version: C{dict} of C{str}
        @param vendor: distribution vendor
        @type vendor: C{str}
        @return: version tag

        >>> RpmGitRepository.version_to_tag("packaging/%(version)s", dict(epoch='0', upstreamversion='0~0'))
        'packaging/0%0_0'
        >>> RpmGitRepository.version_to_tag("%(vendor)s/v%(version)s", dict(upstreamversion='1.0', release='2'), "myvendor")
        'myvendor/v1.0-2'
        """
        version_tag = format % dict(version,
                                    version=RpmPkgPolicy.compose_full_version(version),
                                    vendor=vendor)
        return RpmGitRepository._sanitize_tag(version_tag)

    @staticmethod
    def _sanitize_tag(tag):
        """sanitize a version so git accepts it as a tag

        >>> RpmGitRepository._sanitize_tag("0.0.0")
        '0.0.0'
        >>> RpmGitRepository._sanitize_tag("0.0~0")
        '0.0_0'
        >>> RpmGitRepository._sanitize_tag("0:0.0")
        '0%0.0'
        >>> RpmGitRepository._sanitize_tag("0%0~0")
        '0%0_0'
        """
        return tag.replace('~', '_').replace(':', '%')

    @property
    def pristine_tar_branch(self):
        """
        The name of the pristine-tar branch, whether it already exists or
        not.
        """
        return PristineTar.branch

    def has_pristine_tar_branch(self):
        """
        Wheter the repo has a I{pristine-tar} branch.

        @return: C{True} if the repo has pristine-tar commits already, C{False}
            otherwise
        @rtype: C{Bool}
        """
        return True if self.has_branch(self.pristine_tar_branch) else False

# vim:et:ts=4:sw=4:et:sts=4:ai:set list listchars=tab\:»·,trail\:·:
