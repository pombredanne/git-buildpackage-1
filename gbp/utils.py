# vim: set fileencoding=utf-8 :
#
# (C) 2006,2007,2010-2012 Guido Guenther <agx@sigxcpu.org>
# (C) 2012 Intel Corporation <eduard.bartosh@linux.intel.com>
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
"""wrappers, utilities, shortcuts"""

import os
import tempfile
import shutil

import gbp.log
from gbp.errors import GbpError

class TempDir(object):
    """
    Create temporary directory.
    Delete it automatically when object is destroyed.

    """

    def __init__(self, suffix='', prefix='tmp', dir=None):
        self.path = None

        if dir is None:
            dir = tempfile.gettempdir()

        target_dir = os.path.abspath(os.path.join(dir, prefix))
        target_dir = os.path.dirname(target_dir)

        try:
            if not os.path.exists(target_dir):
                os.makedirs(target_dir)
            self.path = tempfile.mkdtemp(suffix, prefix, dir)
        except OSError, (e, msg):
            raise GbpError, "Failed to create dir on %s: %s" % (target_dir, msg)

    def __str__(self):
        return self.path

    def __del__(self):
        """Remove it when object is destroyed."""
        if self.path and os.path.exists(self.path):
            gbp.log.debug("Remove temporary directory '%s'" % self.path)
            shutil.rmtree(self.path)
