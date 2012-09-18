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
#
"""Common functionality for Debian and RPM patchqueue management"""

import re
import os
import shutil
import subprocess
from gbp.git import (GitRepositoryError, GitRepository)
from gbp.command_wrappers import (Command, GitCommand, RunAtCommand)
from gbp.errors import GbpError
import gbp.log
from gbp.patch_series import (PatchSeries, Patch)

DEFAULT_PQ_BRANCH_NAME = "patch-queue/%(branch)s"


def is_pq_branch(branch, options):
    """
    is branch a patch-queue branch?

    >>> from optparse import OptionParser
    >>> (opts, args) = OptionParser().parse_args([])
    >>> is_pq_branch("foo", opts)
    False
    >>> is_pq_branch("patch-queue/foo", opts)
    True
    >>> opts.pq_branch = "%(branch)s/development"
    >>> is_pq_branch("foo/development/bar", opts)
    False
    >>> is_pq_branch("bar/foo/development", opts)
    True
    >>> opts.pq_branch = "development"
    >>> is_pq_branch("development", opts)
    True
    >>> opts.pq_branch = "my/%(branch)s/pq"
    >>> is_pq_branch("my/foo/pqb", opts)
    False
    >>> is_pq_branch("my/foo/pq", opts)
    True
    """
    pq_format_str = DEFAULT_PQ_BRANCH_NAME
    if hasattr(options, "pq_branch"):
        pq_format_str = options.pq_branch

    pq_re = re.compile(r'^%s$' % (pq_format_str % dict(branch="(?P<base>\S+)")))
    if pq_re.match(branch):
        return True
    return False


def pq_branch_name(branch, options):
    """
    get the patch queue branch corresponding to branch

    >>> from optparse import OptionParser
    >>> (opts, args) = OptionParser().parse_args([])
    >>> pq_branch_name("patch-queue/master", opts)
    >>> pq_branch_name("foo", opts)
    'patch-queue/foo'
    >>> opts.pq_branch = "%(branch)s/development"
    >>> pq_branch_name("foo", opts)
    'foo/development'
    >>> opts.pq_branch = "development"
    >>> pq_branch_name("foo", opts)
    'development'
    """
    pq_format_str = DEFAULT_PQ_BRANCH_NAME
    if hasattr(options, "pq_branch"):
        pq_format_str = options.pq_branch
        
    if not is_pq_branch(branch, options):
        return pq_format_str % dict(branch=branch)


def pq_branch_base(pq_branch, options):
    """
    Get the branch corresponding to the given patch queue branch.
    Returns the packaging/debian branch if pq format string doesn't contain
    '%(branch)s' key.

    >>> from optparse import OptionParser
    >>> (opts, args) = OptionParser().parse_args([])
    >>> opts.packaging_branch = "packaging"
    >>> pq_branch_base("patch-queue/master", opts)
    'master'
    >>> pq_branch_base("foo", opts)
    >>> opts.pq_branch = "my/%(branch)s/development"
    >>> pq_branch_base("foo/development", opts)
    >>> pq_branch_base("my/foo/development/bar", opts)
    >>> pq_branch_base("my/foo/development", opts)
    'foo'
    >>> opts.pq_branch = "development"
    >>> pq_branch_base("foo/development", opts)
    >>> pq_branch_base("development", opts)
    'packaging'
    """
    pq_format_str = DEFAULT_PQ_BRANCH_NAME
    if hasattr(options, "pq_branch"):
        pq_format_str = options.pq_branch

    pq_re = re.compile(r'^%s$' % (pq_format_str % dict(branch="(?P<base>\S+)")))
    m = pq_re.match(pq_branch)
    if m:
        if 'base' in m.groupdict():
            return m.group('base')
        return options.packaging_branch


def get_maintainer_from_control():
    """Get the maintainer from the control file"""
    cmd = 'sed -n -e \"s/Maintainer: \\+\\(.*\\)/\\1/p\" debian/control'
    cmdout = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE).stdout.readlines()

    if len(cmdout) > 0:
        maintainer = cmdout[0].strip()
        m = re.match('(?P<name>.*[^ ]) *<(?P<email>.*)>', maintainer)
        if m:
            return m.group('name'), m.group('email')

    return None, None


def switch_to_pq_branch(repo, branch, options):
    """
    Switch to patch-queue branch if not already there, create it if it
    doesn't exist yet
    """
    if is_pq_branch (branch, options):
        return

    pq_branch = pq_branch_name(branch, options)
    if not repo.has_branch(pq_branch):
        try:
            repo.create_branch(pq_branch)
        except GitRepositoryError:
            raise GbpError("Cannot create patch-queue branch '%s'. Try 'rebase' instead."
                           % pq_branch)

    gbp.log.info("Switching to '%s'" % pq_branch)
    repo.set_branch(pq_branch)


def apply_single_patch(repo, branch, patch, options, topic=None):
    switch_to_pq_branch(repo, branch, options)
    apply_and_commit_patch(repo, patch, topic)


def apply_and_commit_patch(repo, patch, topic=None):
    """apply a single patch 'patch', add topic 'topic' and commit it"""
    author = { 'name': patch.author,
               'email': patch.email,
               'date': patch.date }

    if not (patch.author and patch.email):
        name, email = get_maintainer_from_control()
        if name:
            gbp.log.warn("Patch '%s' has no authorship information, "
                         "using '%s <%s>'" % (patch.path, name, email))
            author['name'] = name
            author['email'] = email
        else:
            gbp.log.warn("Patch %s has no authorship information")

    repo.apply_patch(patch.path, strip=patch.strip)
    tree = repo.write_tree()
    msg = "%s\n\n%s" % (patch.subject, patch.long_desc)
    if topic:
        msg += "\nGbp-Pq-Topic: %s" % topic
    commit = repo.commit_tree(tree, msg, [repo.head], author=author)
    repo.update_ref('HEAD', commit, msg="gbp-pq import %s" % patch.path)


def drop_pq(repo, branch, options):
    if is_pq_branch(branch, options):
        gbp.log.err("On a patch-queue branch, can't drop it.")
        raise GbpError
    else:
        pq_branch = pq_branch_name(branch, options)

    if repo.has_branch(pq_branch):
        repo.delete_branch(pq_branch)
        gbp.log.info("Dropped branch '%s'." % pq_branch)
    else:
        gbp.log.info("No patch queue branch found - doing nothing.")
