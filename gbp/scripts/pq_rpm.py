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
                                   write_patch, switch_to_pq_branch,
                                   apply_single_patch, apply_and_commit_patch,
                                   drop_pq)

def export_patches(repo, branch, options):
    """Export patches from the pq branch into a packaging branch"""
    if is_pq_branch(branch):
        base = pq_branch_base(branch)
        gbp.log.info("On '%s', switching to '%s'" % (branch, base))
        branch = base
        repo.set_branch(branch)

    pq_branch = pq_branch_name(branch)

    # Find and parse .spec file
    try:
      options.packaging_dir, specfilename = guess_spec(options.packaging_dir)
      spec = SpecFile(specfilename)
    except KeyError:
        raise GbpError, "Can't parse spec"
    spec.debugprint()
    if not options.packaging_dir:
      options.packaging_dir = "."

    # Find upstream version
    upstream_commit = repo.find_version(options.upstream_tag, spec.version)
    if not upstream_commit:
        raise GbpError, ("Couldn't find upstream version %s. Don't know on what base to import." % spec.version)

    for n, p in spec.patches.iteritems():
        f = options.packaging_dir+"/"+p['filename']
        gbp.log.debug("Removing '%s'" % f) 
        try:
            os.unlink(f)
        except OSError, (e, msg):
            if e != errno.ENOENT:
                raise GbpError, "Failed to remove patch: %s" % msg
            else:
                gbp.log.debug("%s does not exist." % f)

    gbp.log.info("Exporting patches from git (%s..%s)" % (upstream_commit, pq_branch))
    patches = repo.format_patches(upstream_commit, pq_branch, options.packaging_dir,
                                  signature=False)

    filenames = []
    if patches:
        gbp.log.info("Regenerating patch queue in '%s'." % options.packaging_dir)
        for patch in patches:
            filenames.append(os.path.basename(write_patch(patch, options.packaging_dir, options)))

        spec.updatepatches(filenames)
        GitCommand('status')(['--', options.packaging_dir])
    else:
        gbp.log.info("No patches on '%s' - nothing to do." % pq_branch)


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

    if is_pq_branch(branch):
        if options.force:
            branch = pq_branch_base(branch)
            pq_branch = pq_branch_name(branch)
            repo.checkout(branch)
        else:
            gbp.log.err("Already on a patch-queue branch '%s' - doing nothing." % branch)
            raise GbpError
    else:
        pq_branch = pq_branch_name(branch)

    if repo.has_branch(pq_branch):
        if options.force:
            drop_pq(repo, branch)
        else:
            raise GbpError, ("Patch queue branch '%s'. already exists. Try 'rebase' instead."
                             % pq_branch)

    # Find and parse .spec file
    try:
      options.packaging_dir, specfilename = guess_spec(options.packaging_dir)
      spec = SpecFile(specfilename)
    except KeyError:
        raise GbpError, "Can't parse spec"
    spec.debugprint()

    # Find upstream version
    commit = repo.find_version(options.upstream_tag, spec.version)
    if commit:
        #commits = repo.commits(num=tries, first_parent=True)
        commits=[commit]
    else:
        raise GbpError, ("Couldn't find upstream version %s. Don't know on what base to import." % spec.version)

    queue = spec.patchseries()
    # Put patches in a safe place
    tmpdir, queue = safe_patches(queue)
    for commit in commits:
        try:
            gbp.log.info("Trying to apply patches at '%s'" % commit)
            repo.create_branch(pq_branch, commit)
        except CommandExecFailed:
            raise GbpError, ("Cannot create patch-queue branch '%s'." % pq_branch)

        repo.set_branch(pq_branch)
        for patch in queue:
            gbp.log.debug("Applying %s" % patch.path)
            try:
                apply_and_commit_patch(repo, patch, patch.topic)
            except (GbpError, GitRepositoryError, CommandExecFailed):
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

    # Edit spec file
    repo.set_branch(branch)
    if spec.putautoupdatemarkers() != 0:
      GitCommand('status')(['--', options.packaging_dir])
      gbp.log.warn("Auto-added gbp autoupdate markers to spec file. Checking the changes manually before git commit is recommended.")

    return os.path.basename(spec.specfile)


def rebase_pq(repo, branch, options):
    if is_pq_branch(branch):
        base = pq_branch_base(branch)
        gbp.log.info("On '%s', switching to '%s'" % (branch, base))
        branch = base
        repo.set_branch(branch)

    # Find and parse .spec file
    try:
        options.packaging_dir, specfile = guess_spec(options.packaging_dir)
        spec = SpecFile(specfile)
    except KeyError:
        raise GbpError, "Can't parse spec"

    # Find upstream version
    upstream_commit = repo.find_version(options.upstream_tag, spec.version)
    if not upstream_commit:
        raise GbpError, ("Couldn't find upstream version %s. Don't know on what base to import." % spec.version)

    switch_to_pq_branch(repo, branch)
    GitCommand("rebase")([upstream_commit])


def switch_pq(repo, current):
    """Switch to patch-queue branch if on base branch and vice versa"""
    if is_pq_branch(current):
        base = pq_branch_base(current)
        gbp.log.info("Switching to %s" % base)
        repo.checkout(base)
    else:
        switch_to_pq_branch(repo, current)


def main(argv):
    retval = 0

    parser = GbpOptionParserRpm(command=os.path.basename(argv[0]), prefix='',
                                usage="%prog [options] action - maintain patches on a patch queue branch\n"
        "Actions:\n"
        "  export         export the patch queue associated to the current branch\n"
        "                 into a quilt patch series in debian/patches/ and update the\n"
        "                 series file.\n"
        "  import         create a patch queue branch from .spec and patches in current dir.\n"
        "  rebase         switch to patch queue branch associated to the current\n"
        "                 branch and rebase against current branch.\n"
        "  drop           drop (delete) the patch queue associated to the current branch.\n"
        "  apply          apply a patch\n"
        "  switch         switch to patch-queue branch and vice versa")
    parser.add_boolean_config_file_option(option_name="patch-numbers", dest="patch_numbers")
    parser.add_option("-v", "--verbose", action="store_true", dest="verbose", default=False,
                      help="verbose command execution")
    parser.add_option("--topic", dest="topic", help="in case of 'apply' topic (subdir) to put patch into")
#    parser.add_config_file_option(option_name="time-machine", dest="time_machine", type="int")
    parser.add_option("--force", dest="force", action="store_true", default=False,
                      help="in case of import even import if the branch already exists")
    parser.add_config_file_option(option_name="color", dest="color", type='tristate')
    parser.add_config_file_option(option_name="upstream-tag", dest="upstream_tag")
    parser.add_config_file_option(option_name="packaging-dir", dest="packaging_dir")

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
            drop_pq(repo, current)
        elif action == "rebase":
            rebase_pq(repo, current, options)
        elif action == "apply":
            patch = Patch(patchfile)
            apply_single_patch(repo, current, patch, options.topic)
        elif action == "switch":
            switch_pq(repo, current)
    except CommandExecFailed:
        retval = 1
    except GbpError, err:
        if len(err.__str__()):
            gbp.log.err(err)
        retval = 1

    return retval

if __name__ == '__main__':
    sys.exit(main(sys.argv))

