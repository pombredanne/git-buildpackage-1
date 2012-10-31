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
"""Test module for RPM command line tools of the git-buildpackage suite"""

import os
from nose.plugins.skip import SkipTest

from gbp.git import GitRepository, GitRepositoryError

DATA_DIR = os.path.abspath(os.path.join('tests', 'TestRpmTools', 'testdata'))

class RpmTestGitRepository(GitRepository):
    """Git repository class for RPM tool tests"""

    def submodule_status(self):
        """
        Check current submodule status
        """
        out, err, ret = self._git_inout('submodule', ['status'],
                                        capture_stderr=True)
        if ret:
            raise GitRepositoryError("Cannot get submodule status: %s" %
                                     err.strip())
        submodules = {}
        for line in out.splitlines():
            module = line.strip()
            # Uninitialized
            status = module[0]
            if status == '-':
                sha1, path = module[1:].rsplit(' ', 1)
            else:
                commitpath = module[1:].rsplit(' ', 1)[0]
                sha1, path = commitpath.split(' ', 1)
            submodules[path] = (status, sha1)
        return submodules


def setup():
    """Module setup"""
    # Check if the testdata is up to date
    repo = RpmTestGitRepository('.')
    submodules = repo.submodule_status()
    status = submodules[os.path.join('tests', 'TestRpmTools', 'testdata')]
    if status[0] == '-':
        raise SkipTest("Skipping '%s', testdata directory not initialized. "\
                       "Consider doing 'git submodule update'" % __name__)

# vim:et:ts=4:sw=4:et:sts=4:ai:set list listchars=tab\:»·,trail\:·:
