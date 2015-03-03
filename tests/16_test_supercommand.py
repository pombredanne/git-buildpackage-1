# vim: set fileencoding=utf-8 :
# (C) 2013 Guido Günther <agx@sigxcpu.org>
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
"""Test L{gbp} command wrapper"""

import sys
# Try unittest2 for CentOS
try:
    import unittest2 as unittest
except ImportError:
    import unittest
import gbp.scripts.supercommand

class TestSuperCommand(unittest.TestCase):

    def test_import(self):
        """Test the importer itself"""
        self.assertRaises(ImportError,
                          gbp.scripts.supercommand.import_command,
                          'not.allowed')
        self.assertRaises(ImportError,
                          gbp.scripts.supercommand.import_command,
                          'not/allowed')
        self.assertRaises(ImportError,
                          gbp.scripts.supercommand.import_command,
                          '0notallowed')
        self.assertIsNotNone(gbp.scripts.supercommand.import_command('pq'))

    def test_invalid_command(self):
        """Test if we fail correctly with an invalid command"""
        old_stderr = sys.stderr
        with open('/dev/null', 'w') as sys.stderr:
            self.assertEqual(gbp.scripts.supercommand.supercommand(
                             ['argv0', 'asdf']), 2)
            self.assertEqual(gbp.scripts.supercommand.supercommand(
                             ['argv0', 'asdf', '--verbose']), 2)
        sys.stderr = old_stderr

    def test_help_command(self):
        """Invoking with --help must not raise an error"""
        self.assertEqual(gbp.scripts.supercommand.supercommand(
                         ['argv0', '--help']), 0)

    def test_missing_arg(self):
        self.assertEqual(gbp.scripts.supercommand.supercommand(
                         ['argv0']), 1)

