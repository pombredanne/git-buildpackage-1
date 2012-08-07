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
"""manage patches in a patch queue"""

import errno
import os
import shutil
import sys
import re
import gzip
import subprocess
import gbp.tmpfile as tempfile
from gbp.config import (GbpOptionParserRpm, GbpOptionGroup)
from gbp.rpm.git import (GitRepositoryError, RpmGitRepository)
from gbp.command_wrappers import (Command, GitCommand, RunAtCommand,
                                  CommandExecFailed)
from gbp.errors import GbpError
import gbp.log
from gbp.patch_series import (PatchSeries, Patch)
from gbp.pkg import parse_archive_filename
from gbp.rpm import (SpecFile, guess_spec, string_to_int)
from gbp.scripts.common.pq import (is_pq_branch, pq_branch_name, pq_branch_base,
                                   switch_to_pq_branch, apply_single_patch,
                                   apply_and_commit_patch, drop_pq)

def compress_patches(patches, compress_size=0):
    """
    Rename and/or compress patches
    """
    ret_patches = []
    for num, patch in enumerate(patches):
        # Compress if patch file is larger than "threshold" value
        if compress_size and os.path.getsize(patch) > compress_size:
            gbp.log.debug("Compressing %s" % os.path.basename(patch))
            subprocess.Popen(['gzip', '-n', patch]).communicate()
            patch += ".gz"

        ret_patches.append(os.path.basename(patch))

    return ret_patches


def write_diff_file(repo, diff_filename, start, end):
    """
    Write diff between two tree-ishes into a file
    """
    try:
        diff = repo.diff(start, end)
        if diff:
            diff_file = open(diff_filename, 'w+')
            diff_file.writelines(diff)
            diff_file.close()
            return diff_filename
        else:
            gbp.log.debug("I won't generate empty diff %s" % diff_filename)
            return None
    except IOError:
        raise GbpError, "Unable to create diff file"


def patch_content_filter(f_in, f_out):
    # Skip the first line that contains commits SHA2
    f_in.readline()
    f_out.writelines(f_in)


def patch_fn_filter(commit_info, patch_number=None, ignore_regex=None,
                    topic_regex=None):
    """
    Create a patch filename, return None if patch is to be ignored.
    """
    suffix = ".patch"
    topic = ""
    # Filter based on subject
    if ignore_regex and re.match(ignore_regex, commit_info['subject']):
        gbp.log.debug("Ignoring commit %s, subject matches ignore-regex" %
                      commit_info['id'])
        return None
    # Parse commit message
    for line in commit_info['body'].splitlines():
        if ignore_regex and re.match(ignore_regex, line):
            gbp.log.debug("Ignoring commit %s, commit message matches "\
                          "ignore-regex" % commit_info['id'])
            return None
        if topic_regex:
                match = re.match(topic_regex, line, flags=re.I)
                if match:
                    gbp.log.debug("Topic %s found for %s" %
                                  (match.group['topic'], commit_info['id']))
                    topic = match.group['topic'] + os.path.sep

    filename = topic
    if patch_number is not None:
        filename += "%04d-" % patch_number
    filename += commit_info['patchname']
    # Truncate filename
    filename = filename[:64-len(suffix)]
    filename += suffix

    return filename


def generate_patches(repo, start, squash_point, end, squash_diff_name,
                     outdir, options):
    """
    Generate patch files from git
    """
    gbp.log.info("Generating patches from git (%s..%s)" % (start, end))
    patches = []

    if not repo.has_treeish(start) or not repo.has_treeish(end):
        raise GbpError # git-ls-tree printed an error message already

    start_sha1 = repo.rev_parse("%s^0" % start)
    try:
        end_commit = end
        end_commit_sha1 = repo.rev_parse("%s^0" % end_commit)
    except GitRepositoryError:
        # In case of plain tree-ish objects, assume current branch head is the
        # last commit
        end_commit = "HEAD"
        end_commit_sha1 = repo.rev_parse("%s^0" % end_commit)

    if repo.get_merge_base(start_sha1, end_commit_sha1) != start_sha1:
        raise GbpError, "Start commit '%s' not an ancestor of end " \
                        "commit '%s'" % (start, end_commit)
    rev_list = reversed(repo.get_commits(start, end_commit))
    # Squash commits, if requested
    if squash_point:
        squash_sha1 = repo.rev_parse("%s^0" % squash_point)
        if start_sha1 != squash_sha1:
            if not squash_sha1 in rev_list:
                raise GbpError, "Given squash point '%s' not found in the " \
                                "history of end commit '%s'" % \
                                (squash_point, end_commit)
            # Shorten SHA1s
            squash_sha1 = repo.rev_parse(squash_sha1, short=7)
            start_sha1 = repo.rev_parse(start_sha1, short=7)

            if squash_diff_name:
                diff_filename = squash_diff_name + ".diff"
            else:
                diff_filename = '%s-to-%s.diff' % (start_sha1, squash_sha1)

            gbp.log.info("Squashing commits %s..%s into one monolithic "\
                         "'%s'" % (start_sha1, squash_sha1, diff_filename))
            diff_filepath = os.path.join(outdir, diff_filename)
            if write_diff_file(repo, diff_filepath, start_sha1, squash_sha1):
                patches.append(diff_filepath)
            start = squash_sha1

    # Generate patches
    patch_num = 1 if options.patch_numbers else None
    for commit in rev_list:
        info = repo.get_commit_info(commit)
        patch_fn = patch_fn_filter(info, patch_num,
                                   options.patch_export_ignore_regex)
        if patch_fn:
            patch_fn = repo.format_patch(commit,
                                         os.path.join(outdir, patch_fn),
                                         filter_fn=patch_content_filter,
                                         signature=False)
            if patch_fn:
                patches.append(patch_fn)
            if options.patch_numbers:
                patch_num += 1

    # Generate diff to the tree-ish object
    if end_commit != end:
        diff_filename = '%s.diff' % end
        gbp.log.info("Generating '%s' (%s..%s)" % \
                     (diff_filename, end_commit, end))
        diff_filepath = os.path.join(outdir, diff_filename)
        if write_diff_file(repo, diff_filepath, end_commit, end):
            patches.append(diff_filepath)

    # Compress
    patches = compress_patches(patches, options.patch_export_compress)

    return patches


def update_patch_series(repo, spec, start, end, options):
    """
    Export patches to packaging directory and update spec file accordingly.
    """
    tmpdir = tempfile.mkdtemp(dir=options.tmp_dir, prefix='patchexport_')
    # Create "vanilla" patches
    squash = options.patch_export_squash_until.split(':', 1)
    squash_point = squash[0]
    squash_name = squash[1] if len(squash) > 1 else ""

    # Remove all old patches from packaging dir
    for n, p in spec.patches.iteritems():
        if p['autoupdate']:
            f = os.path.join(spec.specdir, p['filename'])
            gbp.log.debug("Removing '%s'" % f)
            try:
                os.unlink(f)
            except OSError, (e, msg):
                if e != errno.ENOENT:
                    raise GbpError, "Failed to remove patch: %s" % msg
                else:
                    gbp.log.debug("%s does not exist." % f)

    patches = generate_patches(repo, start, squash_point, end, squash_name,
                               spec.specdir, options)

    filenames = [os.path.basename(patch) for patch in patches]
    spec.update_patches(filenames)
    spec.write_spec_file()


def export_patches(repo, branch, options):
    """Export patches from the pq branch into a packaging branch"""
    if is_pq_branch(branch, options):
        base = pq_branch_base(branch, options)
        gbp.log.info("On '%s', switching to '%s'" % (branch, base))
        branch = base
        repo.set_branch(branch)

    pq_branch = pq_branch_name(branch, options)

    # Find and parse .spec file
    try:
        if options.spec_file != 'auto':
            specfilename = options.spec_file
            options.packaging_dir = os.path.dirname(specfilename)
        else:
            specfilename = guess_spec(options.packaging_dir,
                                      True,
                                      os.path.basename(repo.path) + '.spec')
        spec = SpecFile(specfilename)
    except KeyError:
        raise GbpError, "Can't parse spec"
    spec.debugprint()

    # Find upstream version
    tag_str_fields = dict(upstreamversion=spec.upstreamversion, vendor="Upstream")
    upstream_commit = repo.find_version(options.upstream_tag, tag_str_fields)
    if not upstream_commit:
        raise GbpError, ("Couldn't find upstream version %s. Don't know on what base to import." % spec.upstreamversion)

    if options.export_rev:
        export_treeish = options.export_rev
    else:
        export_treeish = pq_branch
    if not repo.has_treeish(export_treeish):
        raise GbpError # git-ls-tree printed an error message already

    update_patch_series(repo, spec, upstream_commit, export_treeish, options)

    GitCommand('status')(['--', spec.specdir])


def safe_patches(queue, tmpdir_base):
    """
    Safe the current patches in a temporary directory
    below 'tmpdir_base'. Also, uncompress compressed patches here.

    @param queue: an existing patch queue
    @param tmpdir_base: base under which to create tmpdir
    @return: tmpdir and a safed queue (with patches in tmpdir)
    @rtype: tuple
    """

    tmpdir = tempfile.mkdtemp(dir=tmpdir_base, prefix='patchimport_')
    safequeue=PatchSeries()

    if len(queue) > 0:
        gbp.log.debug("Safeing patches '%s' in '%s'" % (os.path.dirname(queue[0].path), tmpdir))
        for p in queue:
            (base, archive_fmt, comp) = parse_archive_filename(p.path)
            if comp == 'gzip':
                gbp.log.debug("Uncompressing '%s'" % os.path.basename(p.path))
                src = gzip.open(p.path, 'r')
                dst_name = os.path.join(tmpdir, os.path.basename(base))
            elif comp:
                raise GbpError, ("Unsupported compression of a patch, giving up")
            else:
                src = open(p.path, 'r')
                dst_name = os.path.join(tmpdir, os.path.basename(p.path))

            dst = open(dst_name, 'w')
            dst.writelines(src)
            src.close()
            dst.close()

            safequeue.append(p)
            safequeue[-1].path = dst_name;

    return (tmpdir, safequeue)


def import_spec_patches(repo, branch, tries, options):
    """
    apply a series of patches in a spec/packaging dir to branch
    the patch-queue branch for 'branch'

    @param repo: git repository to work on
    @param branch: branch to base pqtch queue on
    @param tries: try that many times to apply the patches going back one
                  commit in the branches history after each failure.
    @param options: command options
    """
    tmpdir = None

    if is_pq_branch(branch, options):
        if options.force:
            branch = pq_branch_base(branch, options)
            pq_branch = pq_branch_name(branch, options)
            repo.checkout(branch)
        else:
            gbp.log.err("Already on a patch-queue branch '%s' - doing nothing." % branch)
            raise GbpError
    else:
        pq_branch = pq_branch_name(branch, options)

    if repo.has_branch(pq_branch):
        if options.force:
            drop_pq(repo, branch, options)
        else:
            raise GbpError, ("Patch queue branch '%s'. already exists. Try 'rebase' instead."
                             % pq_branch)

    # Find and parse .spec file
    try:
        if options.spec_file != 'auto':
            specfilename = options.spec_file
            options.packaging_dir = os.path.dirname(specfilename)
        else:
            specfilename = guess_spec(options.packaging_dir,
                                      True,
                                      os.path.basename(repo.path) + '.spec')
        spec = SpecFile(specfilename)
    except KeyError:
        raise GbpError, "Can't parse spec"
    spec.debugprint()

    # Find upstream version
    tag_str_fields = dict(upstreamversion=spec.upstreamversion, vendor="Upstream")
    commit = repo.find_version(options.upstream_tag, tag_str_fields)
    if commit:
        #commits = repo.commits(num=tries, first_parent=True)
        commits=[commit]
    else:
        raise GbpError, ("Couldn't find upstream version %s. Don't know on what base to import." % spec.upstreamversion)

    queue = spec.patchseries()
    # Put patches in a safe place
    tmpdir, queue = safe_patches(queue, options.tmp_dir)
    for commit in commits:
        try:
            gbp.log.info("Trying to apply patches at '%s'" % commit)
            repo.create_branch(pq_branch, commit)
        except GitRepositoryError:
            raise GbpError, ("Cannot create patch-queue branch '%s'." % pq_branch)

        repo.set_branch(pq_branch)
        for patch in queue:
            gbp.log.debug("Applying %s" % patch.path)
            try:
                apply_and_commit_patch(repo, patch)
            except (GbpError, GitRepositoryError):
                repo.set_branch(branch)
                repo.delete_branch(pq_branch)
                break
        else:
            # All patches applied successfully
            break
    else:
        raise GbpError, "Couldn't apply patches"

    repo.set_branch(branch)

    return os.path.basename(spec.specfile)


def rebase_pq(repo, branch, options):
    if is_pq_branch(branch, options):
        base = pq_branch_base(branch, options)
        gbp.log.info("On '%s', switching to '%s'" % (branch, base))
        branch = base
        repo.set_branch(branch)

    # Find and parse .spec file
    try:
        if options.spec_file != 'auto':
            specfilename = options.spec_file
            options.packaging_dir = os.path.dirname(specfile)
        else:
            specfilename = guess_spec(options.packaging_dir,
                                      True,
                                      os.path.basename(repo.path) + '.spec')
        spec = SpecFile(specfilename)
    except KeyError:
        raise GbpError, "Can't parse spec"

    # Find upstream version
    tag_str_fields = dict(upstreamversion=spec.upstreamversion, vendor="Upstream")
    upstream_commit = repo.find_version(options.upstream_tag, tag_str_fields)
    if not upstream_commit:
        raise GbpError, ("Couldn't find upstream version %s. Don't know on what base to import." % spec.upstreamversion)

    switch_to_pq_branch(repo, branch, options)
    GitCommand("rebase")([upstream_commit])


def switch_pq(repo, current, options):
    """Switch to patch-queue branch if on base branch and vice versa"""
    if is_pq_branch(current, options):
        base = pq_branch_base(current, options)
        gbp.log.info("Switching to %s" % base)
        repo.checkout(base)
    else:
        switch_to_pq_branch(repo, current, options)


def main(argv):
    retval = 0

    parser = GbpOptionParserRpm(command=os.path.basename(argv[0]), prefix='',
                                usage="%prog [options] action - maintain patches on a patch queue branch\n"
        "Actions:\n"
        "  export         Export the patch queue / devel branch associated to the\n"
        "                 current branch into a patch series in and update the spec file\n"
        "  import         Create a patch queue / devel branch from spec file\n"
        "                 and patches in current dir.\n"
        "  rebase         Switch to patch queue / devel branch associated to the current\n"
        "                 branch and rebase against upstream.\n"
        "  drop           Drop (delete) the patch queue /devel branch associated to\n"
        "                 the current branch.\n"
        "  apply          Apply a patch\n"
        "  switch         Switch to patch-queue branch and vice versa")
    parser.add_boolean_config_file_option(option_name="patch-numbers", dest="patch_numbers")
    parser.add_option("-v", "--verbose", action="store_true", dest="verbose", default=False,
                      help="Verbose command execution")
    parser.add_option("--force", dest="force", action="store_true", default=False,
                      help="In case of import even import if the branch already exists")
    parser.add_config_file_option(option_name="vendor", action="store", dest="vendor")
    parser.add_config_file_option(option_name="color", dest="color", type='tristate')
    parser.add_config_file_option(option_name="tmp-dir", dest="tmp_dir")
    parser.add_config_file_option(option_name="upstream-tag", dest="upstream_tag")
    parser.add_config_file_option(option_name="spec-file", dest="spec_file")
    parser.add_config_file_option(option_name="packaging-dir", dest="packaging_dir")
    parser.add_config_file_option(option_name="packaging-branch",
                                  dest="packaging_branch",
                                  help="Branch the packaging is being maintained on. Only relevant if a invariable/single pq-branch is defined, in which case this is used as the 'base' branch. Default is '%(packaging-branch)s'")
    parser.add_config_file_option(option_name="pq-branch", dest="pq_branch")
    parser.add_option("--export-rev", action="store", dest="export_rev", default="",
                      help="Export patches from treeish object TREEISH instead of head of patch-queue branch", metavar="TREEISH")
    parser.add_config_file_option("patch-export-compress", dest="patch_export_compress")
    parser.add_config_file_option("patch-export-squash-until", dest="patch_export_squash_until")
    parser.add_config_file_option("patch-export-ignore-regex", dest="patch_export_ignore_regex")

    (options, args) = parser.parse_args(argv)
    gbp.log.setup(options.color, options.verbose)
    options.patch_export_compress = string_to_int(options.patch_export_compress)

    if len(args) < 2:
        gbp.log.err("No action given.")
        return 1
    else:
        action = args[1]

    if args[1] in ["export", "import", "rebase", "drop", "switch"]:
        pass
    elif args[1] in ["apply"]:
        if len(args) != 3:
            gbp.log.err("No patch name given.")
            return 1
        else:
            patchfile = args[2]
    else:
        gbp.log.err("Unknown action '%s'." % args[1])
        return 1

    try:
        repo = RpmGitRepository(os.path.curdir)
    except GitRepositoryError:
        gbp.log.err("%s is not a git repository" % (os.path.abspath('.')))
        return 1

    if os.path.abspath('.') != repo.path:
        gbp.log.warn("Switching to topdir before running commands")
        os.chdir(repo.path)

    try:
        # Create base temporary directory for this run
        options.tmp_dir = tempfile.mkdtemp(dir=options.tmp_dir,
                                           prefix='gbp-pq-rpm_')
        current = repo.get_branch()
        if action == "export":
            export_patches(repo, current, options)
        elif action == "import":
#            tries = options.time_machine if (options.time_machine > 0) else 1
            tries = 1
            specfile=import_spec_patches(repo, current, tries, options)
            current = repo.get_branch()
            gbp.log.info("Patches listed in '%s' imported on '%s'" %
                          (specfile, current))
        elif action == "drop":
            drop_pq(repo, current, options)
        elif action == "rebase":
            rebase_pq(repo, current, options)
        elif action == "apply":
            patch = Patch(patchfile)
            apply_single_patch(repo, current, patch, options)
        elif action == "switch":
            switch_pq(repo, current, options)
    except CommandExecFailed:
        retval = 1
    except GitRepositoryError as err:
        gbp.log.err("Git command failed: %s" % err)
        ret = 1
    except GbpError, err:
        if len(err.__str__()):
            gbp.log.err(err)
        retval = 1
    finally:
        shutil.rmtree(options.tmp_dir, ignore_errors=True)

    return retval

if __name__ == '__main__':
    sys.exit(main(sys.argv))

