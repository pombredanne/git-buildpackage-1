# vim: set fileencoding=utf-8 :

import os
import sys
# Try unittest2 for CentOS
try:
    import unittest2 as unittest
except ImportError:
    import unittest
from gbp.config import GbpOptionParser, GbpOptionGroup
from .testutils import GbpLogTester

class TestConfigParser(unittest.TestCase, GbpLogTester):
    def __init__(self, methodName='runTest'):
        unittest.TestCase.__init__(self, methodName)
        GbpLogTester.__init__(self)

    def setUp(self):
        self.conffiles_save = os.environ.get('GBP_CONF_FILES')
        self.confname = 'tests/data/test1.conf'
        self.assertTrue(os.stat(self.confname))
        os.environ['GBP_CONF_FILES'] = self.confname
        self._capture_log(True)

    def tearDown(self):
        if self.conffiles_save:
            os.environ['GBP_CONF_FILES'] = self.conffiles_save
        self._capture_log(False)

    def test_default(self):
        """
        A value only in the default section should be available in all commands
        """
        for n in range(1,5):
            for prefix in [ '', 'git-', 'gbp-' ]:
                parser = GbpOptionParser('cmd%d' % n)
                self.assertEqual(parser.config['default_option'], 'default_default1')

    def test_single_override(self):
        """
        A value in any command section should override the default
        """
        for prefix in [ '', 'git-', 'gbp-' ]:
            parser = GbpOptionParser('%scmd1' % prefix)
            self.assertEqual(parser.config['single_override_option1'], 'single_override_value1')
        # No deprecation warning since we test1.conf section is [cmd1]
        self.assertEqual(self._get_log(), [])

    def test_single_git_override(self):
        """
        A value in any git-command section should override the default
        """
        for prefix in [ '', 'git-' ]:
            parser = GbpOptionParser('%scmd2' % prefix)
            self.assertEqual(parser.config['single_git_override_option1'], 'single_git_override_value1')
        for line in range(0,2):
            self._check_log(line, ".*Old style config section \[git-cmd2\] found please rename to \[cmd2\]")

    def test_single_gbp_override(self):
        """
        A value in any gbp-command section should override the default
        """
        for prefix in [ '', 'gbp-' ]:
            parser = GbpOptionParser('%scmd3' % prefix)
            self.assertEqual(parser.config['single_gbp_override_option1'], 'single_gbp_override_value1')
        for line in range(0,2):
            self._check_log(line, ".*Old style config section \[gbp-cmd3\] found please rename to \[cmd3\]")


    def test_new_overrides_git(self):
        """
        A value in the cmd section should override the old git-cmd section independent from
        how we're invoked
        """
        for n in range(4, 6):
            for prefix in [ '', 'git-']:
                cmd = '%scmd%d' % (prefix, n)
                parser = GbpOptionParser(cmd)
                actual = parser.config['new_overrides_git_option1']
                expected = 'new_overrides_git_value1'
                self.assertEqual(actual, expected, "%s != %s for %s" % (actual, expected, cmd))

    def test_get_config_file_value(self):
        """
        Read a single value from the parsed config
        """
        parser = GbpOptionParser('cmd4')
        self.assertEqual(parser.get_config_file_value('new_overrides_git_option1'),
                         'new_overrides_git_value1')
        self.assertEqual(parser.get_config_file_value('doesnotexist'), None)

    def test_param_list(self):
        parser = GbpOptionParser('cmd4')

        branch_group = GbpOptionGroup(parser, "branch options", "branch update and layout options")
        parser.add_option_group(branch_group)
        branch_group.add_config_file_option(option_name="upstream-branch", dest="upstream_branch")
        branch_group.add_config_file_option("debian-branch", dest="upstream_branch")
        parser.add_config_file_option(option_name="color", dest="color", type='tristate')

        params = parser.valid_options
        self.assertTrue('upstream-branch' in params)
        self.assertTrue('debian-branch' in params)
        self.assertTrue('color' in params)
