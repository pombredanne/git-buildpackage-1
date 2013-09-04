# vim: set fileencoding=utf-8 :
#
# (C) 2006, 2007, 2009, 2011 Guido Guenther <agx@sigxcpu.org>
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
#
"""Common functionality for import-orig scripts"""
import os
import tempfile
import gbp.command_wrappers as gbpc
from gbp.pkg import UpstreamSource
import gbp.log

# Try to import readline, since that will cause raw_input to get fancy
# line editing and history capabilities. However, if readline is not
# available, raw_input will still work.
try:
    import readline
except ImportError:
    pass


def orig_needs_repack(upstream_source, options):
    """
    Determine if the upstream sources needs to be repacked

    We repack if
     1. we want to filter out files and use pristine tar since we want
        to make a filtered tarball available to pristine-tar
     2. when we don't have a suitable upstream tarball (e.g. zip archive or unpacked dir)
        and want to use filters
     3. when we don't have a suitable upstream tarball (e.g. zip archive or unpacked dir)
        and want to use pristine-tar
    """
    if ((options.pristine_tar and options.filter_pristine_tar and len(options.filters) > 0)):
        return True
    elif not upstream_source.is_tarball():
        if len(options.filters):
            return True
        elif options.pristine_tar:
            return True
    return False


def cleanup_tmp_tree(tree):
    """remove a tree of temporary files"""
    try:
        gbpc.RemoveTree(tree)()
    except gbpc.CommandExecFailed:
        gbp.log.err("Removal of tmptree %s failed." % tree)


def is_link_target(target, link):
    """does symlink link already point to target?"""
    if os.path.exists(link):
            if os.path.samefile(target, link):
                return True
    return False


def ask_package_name(default, name_validator_func, err_msg):
    """
    Ask the user for the source package name.
    @param default: The default package name to suggest to the user.
    """
    while True:
        sourcepackage = raw_input("What will be the source package name? [%s] " % default)
        if not sourcepackage: # No input, use the default.
            sourcepackage = default
        # Valid package name, return it.
        if name_validator_func(sourcepackage):
            return sourcepackage

        # Not a valid package name. Print an extra
        # newline before the error to make the output a
        # bit clearer.
        gbp.log.warn("\nNot a valid package name: '%s'.\n%s" % (sourcepackage, err_msg))


def ask_package_version(default, ver_validator_func, err_msg):
    """
    Ask the user for the upstream package version.
    @param default: The default package version to suggest to the user.
    """
    while True:
        version = raw_input("What is the upstream version? [%s] " % default)
        if not version: # No input, use the default.
            version = default
        # Valid version, return it.
        if ver_validator_func(version):
            return version

        # Not a valid upstream version. Print an extra
        # newline before the error to make the output a
        # bit clearer.
        gbp.log.warn("\nNot a valid upstream version: '%s'.\n%s" % (version, err_msg))


def repack_source(source, new_name, unpack_dir, filters, new_prefix=None):
    """Repack the source tree"""
    repacked = source.pack(new_name, filters, new_prefix)
    if source.is_tarball(): # the tarball was filtered on unpack
        repacked.unpacked = source.unpacked
    else: # otherwise unpack the generated tarball get a filtered tree
        repacked.unpack(unpack_dir)
    return repacked

