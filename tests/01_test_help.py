# vim: set fileencoding=utf-8 :

"""Check if --help works"""

import os
import unittest

class TestHelp(unittest.TestCase):
    """Test help output of gbp commands"""

    def testHelp(self):
        for script in ['buildpackage',
                      'clone',
                      'create_remote_repo',
                      'dch',
                      'import_orig',
                      'import_dsc',
                      'pull',
                      'pq']:
            module = 'gbp.scripts.%s' % script
            m = __import__(module, globals(), locals(), ['main'], -1)
            self.assertRaises(SystemExit,
                              m.main,
                              ['doesnotmatter', '--help'])

    """Test help output of RPM-specific commands"""
    def testHelpRpm(self):
        for script in ['import_srpm',
                       'pq_rpm']:
            module = 'gbp.scripts.%s' % script
            m = __import__(module, globals(), locals(), ['main'], -1)
            self.assertRaises(SystemExit,
                              m.main,
                              ['doesnotmatter', '--help'])

# vim:et:ts=4:sw=4:et:sts=4:ai:set list listchars=tab\:»·,trail\:·:
