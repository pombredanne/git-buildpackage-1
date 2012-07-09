# vim: set fileencoding=utf-8 :
#
# (C) 2006-2011 Guido Guenther <agx@sigxcpu.org>
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
"""Common functionality for Debian and RPM buildpackage scripts"""

import os, os.path
import tempfile
import subprocess
import shutil
from gbp.command_wrappers import (CatenateTarArchive, CatenateZipArchive)
from gbp.git import GitRepository, GitRepositoryError
from gbp.errors import GbpError
import gbp.log

# when we want to reference the index in a treeish context we call it:
index_name = "INDEX"
# when we want to reference the working copy in treeish context we call it:
wc_names = {'WC':           {'force': True, 'untracked': True},
            'WC.TRACKED':   {'force': False, 'untracked': False},
            'WC.UNTRACKED': {'force': False, 'untracked': True},
            'WC.IGNORED':   {'force': True, 'untracked': True}}
# index file name used to export working copy
wc_index = ".git/gbp_index"


def git_archive_submodules(repo, treeish, output, prefix, comp_type, comp_level, comp_opts, format='tar'):
    """
    Create a source tree archive with submodules.

    Since git-archive always writes an end of tarfile trailer we concatenate
    the generated archives using tar and compress the result.

    Exception handling is left to the caller.
    """
    if prefix:
        prefix = prefix.strip('/') + '/'
    tempdir = tempfile.mkdtemp()
    main_archive = os.path.join(tempdir, "main.%s" % format)
    submodule_archive = os.path.join(tempdir, "submodule.%s" % format)
    try:
        # generate main (tmp) archive
        repo.archive(format=format, prefix=prefix,
                     output=main_archive, treeish=treeish)

        # generate each submodule's arhive and append it to the main archive
        for (subdir, commit) in repo.get_submodules(treeish):
            tarpath = [subdir, subdir[2:]][subdir.startswith("./")]
            subrepo = GitRepository(os.path.join(repo.path, subdir))

            gbp.log.debug("Processing submodule %s (%s)" % (subdir, commit[0:8]))
            subrepo.archive(format=format,
                            prefix='%s%s/' % (prefix, tarpath),
                            output=submodule_archive,
                            treeish=commit)
            if format == 'tar':
                CatenateTarArchive(main_archive)(submodule_archive)
            elif format == 'zip':
                CatenateZipArchive(main_archive)(submodule_archive)

        # compress the output
        if comp_type:
            ret = os.system("%s --stdout -%s %s %s > %s" % \
                           (comp_type, comp_level, " ".join(comp_opts),
                            main_archive, output))
            if ret:
                raise GbpError("Error creating %s: %d" % (output, ret))
        else:
            shutil.move(main_archive, output)
    finally:
        shutil.rmtree(tempdir)


def compress_filter(f_in, f_out, comp_type, comp_opts):
    cmd = [comp_type] + comp_opts
    p_filter = subprocess.Popen(cmd,
                                stdin=f_in,
                                stdout=f_out)
    return p_filter.wait()



def git_archive_single(repo, treeish, output, prefix, comp_type, comp_level,
                       comp_opts, format='tar'):
    """
    Create an archive without submodules

    Exception handling is left to the caller.
    """
    if prefix:
        prefix = prefix.strip('/') + '/'
    filter_fn = None
    filter_args = {}
    if comp_type:
        filter_fn = compress_filter
        filter_args = {'comp_type': comp_type,
                       'comp_opts': ['--stdout', '-%s' % comp_level]}
        if comp_opts:
            filter_args['comp_opts'].extend(comp_opts)

    repo.archive(format=format, prefix=prefix, output=output,
                 treeish=treeish, filter_fn=filter_fn, filter_args=filter_args)


def untar_filter(f_in, f_out, target_dir):
    gbp.log.debug("Extracting git output to %s" % target_dir)
    p_filter = subprocess.Popen(['tar', '-C', target_dir, '-x'],
                                stdin=f_in,
                                stdout=f_out)
    return p_filter.wait()


#{ Functions to handle export-dir
def dump_tree(repo, export_dir, treeish, with_submodules):
    """Dump a git tree-ish to output_dir"""
    output_dir = os.path.dirname(os.path.abspath(export_dir))
    prefix = os.path.basename(export_dir)
    try:
        untar_args = {'target_dir': output_dir}
        repo.archive(format="tar", prefix='%s/' % prefix, output=os.devnull,
                     treeish=treeish, filter_fn=untar_filter,
                     filter_args=untar_args)
        if with_submodules and repo.has_submodules():
            repo.update_submodules()
            for (subdir, commit) in repo.get_submodules(treeish):
                gbp.log.info("Processing submodule %s (%s)" % (subdir,
                                                               commit[0:8]))
                tarpath = [subdir, subdir[2:]][subdir.startswith("./")]
                subrepo = GitRepository(os.path.join(repo.path, subdir))
                subrepo.archive(format='tar',
                                prefix='%s/%s/' % (prefix, tarpath),
                                output=os.devnull, treeish=treeish,
                                filter_fn=untar_filter, filter_args=untar_args)
    except GitRepositoryError as err:
        gbp.log.err("Error dumping tree to %s: %s" % (output_dir, err[0]))
        return False
    return True


def write_wc(repo, force=True, untracked=True):
    """write out the current working copy as a treeish object"""
    clone_index()
    repo.add_files(repo.path, force=force, untracked=untracked, index_file=wc_index)
    tree = repo.write_tree(index_file=wc_index)
    return tree


def drop_index():
    """drop our custom index"""
    if os.path.exists(wc_index):
        os.unlink(wc_index)

def clone_index():
    """Copy the current index file to our custom index file"""
    indexfn = ".git/index"
    if os.path.exists(indexfn):
        shutil.copy2(indexfn, wc_index)
