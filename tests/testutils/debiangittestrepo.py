# vim: set fileencoding=utf-8 :

from .. import context

import os
# Try unittest2 for CentOS
try:
    import unittest2 as unittest
except ImportError:
    import unittest

import gbp.deb.git

class DebianGitTestRepo(unittest.TestCase):
    """Scratch repo for a single unit test"""

    def setUp(self):
        self.tmpdir = context.new_tmpdir(__name__)

        repodir = self.tmpdir.join('test_repo')
        self.repo = gbp.deb.git.DebianGitRepository.create(repodir)

    def tearDown(self):
        context.teardown()

    def add_file(self, name, content=None, msg=None):
        """
        Add a single file with name I{name} and content I{content}. If
        I{content} is C{none} the content of the file is undefined.

        @param name: the file's path relativ to the git repo
        @type name: C{str}
        @param content: the file's content
        @type content: C{str}
        """
        path = os.path.join(self.repo.path, name)

        d = os.path.dirname(path)
        if not os.path.exists(d):
            os.makedirs(d)

        with open(path, 'w+') as f:
            content == None or f.write(content)
        self.repo.add_files(name, force=True)
        self.repo.commit_files(path, msg or "added %s" % name)
