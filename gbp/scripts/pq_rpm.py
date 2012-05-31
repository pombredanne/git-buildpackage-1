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
import tempfile
from gbp.config import (GbpOptionParserRpm, GbpOptionGroup)
from gbp.rpm.git import (GitRepositoryError, RpmGitRepository)
from gbp.command_wrappers import (Command, GitCommand, RunAtCommand,
                                  CommandExecFailed)
from gbp.errors import GbpError
import gbp.log
from gbp.patch_series import (PatchSeries, Patch)
from gbp.rpm import (SpecFile, guess_spec)
from gbp.scripts.common.pq import (is_pq_branch, pq_branch_name, pq_branch_base,
                                   switch_to_pq_branch, apply_single_patch,
                                   apply_and_commit_patch, drop_pq)

def write_patch(patch, patch_dir, options):
    """Write the patch exported by 'git-format-patch' to it's final location
       (as specified in the commit)"""
    oldname = os.path.basename(patch)
    newname = oldname
    tmpname = patch + ".gbp"
    old = file(patch, 'r')
    tmp = file(tmpname, 'w')
    in_patch = False
    topic = None

    # Skip first line (From <sha1>)
    old.readline()
    for line in old:
        if line.lower().startswith("gbp-pq-topic: "):
            topic = line.split(" ",1)[1].strip()
            gbp.log.debug("Topic %s found for %s" % (topic, patch))
            continue
        tmp.write(line)
    tmp.close()
    old.close()

    if not options.patch_numbers:
        patch_re = re.compile("[0-9]+-(?P<name>.+)")
        m = patch_re.match(oldname)
        if m:
            newname = m.group('name')

    if topic:
        topicdir = os.path.join(patch_dir, topic)
    else:
        topicdir = patch_dir

    if not os.path.isdir(topicdir):
        os.makedirs(topicdir, 0755)

    os.unlink(patch)
    dstname = os.path.join(topicdir, newname)
    gbp.log.debug("Moving %s to %s" % (tmpname, dstname))
    shutil.move(tmpname, dstname)

    return dstname


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

    if options.export_rev:
        export_treeish = options.export_rev
    else:
        export_treeish = pq_branch
    if not repo.has_treeish(export_treeish):
        raise GbpError # git-ls-tree printed an error message already

    gbp.log.info("Exporting patches from git (%s..%s)" % (upstream_commit, export_treeish))
    patches = repo.format_patches(upstream_commit, export_treeish, spec.specdir,
                                  signature=False)
    filenames = []
    if patches:
        gbp.log.info("Regenerating patch queue in '%s'." % spec.specdir)
        for patch in patches:
            filenames.append(os.path.basename(write_patch(patch, spec.specdir, options)))

        spec.update_patches(filenames)
        spec.write_spec_file()
        GitCommand('status')(['--', spec.specdir])
    else:
        gbp.log.info("No patches on '%s' - nothing to do." % export_treeish)


def safe_patches(queue):
    """
    Safe the current patches in a temporary directory
    below .git/

    @param queue: an existing patch queue
    @return: tmpdir and a safed queue (with patches in tmpdir)
    @rtype: tuple
    """

    tmpdir = tempfile.mkdtemp(dir='.git/', prefix='gbp-pq')
    safequeue=PatchSeries()

    if len(queue) > 0:
        gbp.log.debug("Safeing patches '%s' in '%s'" % (os.path.dirname(queue[0].path), tmpdir))
        for p in queue:
            dst = os.path.join(tmpdir, os.path.basename(p.path))
            shutil.copy(p.path, dst)
            safequeue.append(p)
            safequeue[-1].path = dst;

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
    tmpdir, queue = safe_patches(queue)
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

    if tmpdir:
        gbp.log.debug("Remove temporary patch safe '%s'" % tmpdir)
        shutil.rmtree(tmpdir)

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
    parser.add_config_file_option(option_name="upstream-tag", dest="upstream_tag")
    parser.add_config_file_option(option_name="spec-file", dest="spec_file")
    parser.add_config_file_option(option_name="packaging-dir", dest="packaging_dir")
    parser.add_config_file_option(option_name="packaging-branch",
                                  dest="packaging_branch",
                                  help="Branch the packaging is being maintained on. Only relevant if a invariable/single pq-branch is defined, in which case this is used as the 'base' branch. Default is '%(packaging-branch)s'")
    parser.add_config_file_option(option_name="pq-branch", dest="pq_branch")
    parser.add_option("--export-rev", action="store", dest="export_rev", default="",
                      help="Export patches from treeish object TREEISH instead of head of patch-queue branch", metavar="TREEISH")

    (options, args) = parser.parse_args(argv)
    gbp.log.setup(options.color, options.verbose)

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

    try:
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

    return retval

if __name__ == '__main__':
    sys.exit(main(sys.argv))

