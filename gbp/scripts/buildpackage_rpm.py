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
"""run commands to build an RPM package out of a git repository"""

import ConfigParser
import errno
import os, os.path
import sys
import shutil
import re
from datetime import datetime

import gbp.tmpfile as tempfile
import gbp.rpm as rpm
from gbp.rpm.policy import RpmPkgPolicy
from gbp.command_wrappers import Command, RunAtCommand, CommandExecFailed
from gbp.config import (GbpOptionParserRpm, GbpOptionGroup)
from gbp.rpm.git import (GitRepositoryError, RpmGitRepository)
from gbp.errors import GbpError
import gbp.log
import gbp.notifications
from gbp.scripts.common.buildpackage import (index_name, wc_names,
                                             git_archive_submodules,
                                             git_archive_single, dump_tree,
                                             write_wc, drop_index)
from gbp.pkg import compressor_opts
from gbp.scripts.pq_rpm import update_patch_series, parse_spec
from gbp.scripts.common.pq import is_pq_branch, pq_branch_name, pq_branch_base


class GbpAutoGenerateError(GbpError):
    """Error for tarball and patch-generation failures"""
    pass


def git_archive(repo, spec, output_dir, tmpdir_base, treeish, prefix,
                comp_level, with_submodules):
    "create a compressed orig tarball in output_dir using git_archive"
    comp_opts = ''
    if spec.orig_src['compression']:
        comp_opts = compressor_opts[spec.orig_src['compression']][0]

    output = os.path.join(output_dir, spec.orig_src['filename'])

    # Remove extra slashes from prefix, will be added by git_archive_x funcs
    prefix = prefix.strip('/')
    try:
        if repo.has_submodules(treeish) and with_submodules:
            repo.update_submodules()
            git_archive_submodules(repo, treeish, output, tmpdir_base,
                                   prefix, spec.orig_src['compression'],
                                   comp_level, comp_opts,
                                   spec.orig_src['archive_fmt'])

        else:
            git_archive_single(repo, treeish, output, prefix,
                               spec.orig_src['compression'], comp_level, comp_opts,
                               spec.orig_src['archive_fmt'])
    except (GitRepositoryError, CommandExecFailed):
        gbp.log.err("Error generating submodules' archives")
        return False
    return True


def prepare_upstream_tarball(repo, spec, options, output_dir):
    """
    Make sure we have an upstream tarball. This involves loooking in
    tarball_dir, symlinking or building it.
    """
    # look in tarball_dir first, if found force a symlink to it
    orig_file = spec.orig_src['filename']
    if options.tarball_dir:
        gbp.log.debug("Looking for orig tarball '%s' at '%s'" % (orig_file, options.tarball_dir))
        if not RpmPkgPolicy.symlink_orig(orig_file, options.tarball_dir, output_dir, force=True):
            gbp.log.info("Orig tarball '%s' not found at '%s'" % (orig_file, options.tarball_dir))
        else:
            gbp.log.info("Orig tarball '%s' found at '%s'" % (orig_file, options.tarball_dir))
    # build an orig unless the user forbids it, always build (and overwrite pre-existing) if user forces it
    if options.force_create or (not options.no_create_orig and not RpmPkgPolicy.has_orig(orig_file, output_dir)):
        if not pristine_tar_build_orig(repo, orig_file, output_dir, options):
            upstream_tree = git_archive_build_orig(repo, spec, output_dir, options)
            if options.pristine_tar_commit:
                if repo.pristine_tar.has_commit(orig_file):
                    gbp.log.debug("%s already on pristine tar branch" %
                                  orig_file)
                else:
                    archive = os.path.join(output_dir, orig_file)
                    gbp.log.debug("Adding %s to pristine-tar branch" %
                                  archive)
                    repo.pristine_tar.commit(archive, upstream_tree)


def makedir(dir):
    """Create directory"""
    try:
        os.mkdir(dir)
    except OSError, (e, msg):
        if e != errno.EEXIST:
            raise GbpError, "Cannot create dir %s" % dir
    return dir


def pristine_tar_build_orig(repo, orig_file, output_dir, options):
    """
    build orig using pristine-tar
    @return: True: orig tarball build, False: noop
    """
    if options.pristine_tar:
        if not repo.has_branch(repo.pristine_tar_branch):
            gbp.log.warn('Pristine-tar branch "%s" not found' %
                         repo.pristine_tar.branch)
        try:
            repo.pristine_tar.checkout(os.path.join(output_dir, orig_file))
            return True
        except CommandExecFailed:
            if options.pristine_tar_commit:
                gbp.log.debug("pristine-tar checkout failed, "
                              "will commit tarball due to "
                              "'--pristine-tar-commit'")
            elif not options.force_create:
                raise
    return False

def get_upstream_tree(repo, spec, options):
    """Determine the upstream tree from the given options"""
    if options.upstream_tree.upper() == 'TAG':
        tag_str_fields = dict(upstreamversion=spec.upstreamversion, vendor="Upstream")
        upstream_tree = repo.version_to_tag(options.upstream_tag, tag_str_fields)
    elif options.upstream_tree.upper() == 'BRANCH':
        if not repo.has_branch(options.upstream_branch):
            raise GbpError("%s is not a valid branch" % options.upstream_branch)
        upstream_tree = options.upstream_branch
    else:
        upstream_tree = get_tree(repo, options.upstream_tree)
    if not repo.has_treeish(upstream_tree):
        raise GbpError('Invalid upstream treeish %s' % upstream_tree)
    return upstream_tree


def get_tree(repo, tree_name):
    """
    Get/create a tree-ish to be used for exporting and diffing. Accepts
    special keywords for git index and working copies.
    """
    try:
        if tree_name == index_name:
            # Write a tree of the index
            tree = repo.write_tree()
        elif tree_name in wc_names:
            # Write a tree of the working copy
            tree = write_wc(repo,
                            force=wc_names[tree_name]['force'],
                            untracked=wc_names[tree_name]['untracked'])
        else:
            tree = tree_name
    except GitRepositoryError as err:
        raise GbpError(err)
    if not repo.has_treeish(tree):
        raise GbpError('Invalid treeish object %s' % tree)

    return tree


def get_current_branch(repo):
    """Get the currently checked-out branch"""
    try:
        branch = repo.get_branch()
    except GitRepositoryError:
        branch = None
    return branch


def get_vcs_info(repo, treeish):
    """Get the info for spec vcs tag"""
    info = {}
    try:
        info['tagname'] = repo.describe(treeish, longfmt=True, always=True,
                                        abbrev=40)
        info['commit'] = repo.rev_parse('%s^0' % treeish)
        info['commitish'] = repo.rev_parse('%s' % treeish)
    except GitRepositoryError:
        # If tree is not commit-ish, expect it to be from current HEAD
        info['tagname'] = repo.describe('HEAD', longfmt=True, always=True,
                                        abbrev=40) + '-dirty'
        info['commit'] = repo.rev_parse('HEAD') + '-dirty'
        info['commitish'] = info['commit']
    return info


def guess_export_params(repo, options):
    """Get commit and tree from where to export packaging and patches"""
    tree = None
    branch = None
    if options.export in wc_names.keys() + [index_name, 'HEAD']:
        branch = get_current_branch(repo)
    elif options.export in repo.get_local_branches():
        branch = options.export
    if branch:
        if is_pq_branch(branch, options):
            packaging_branch = pq_branch_base(branch, options)
            if repo.has_branch(packaging_branch):
                gbp.log.info("It seems you're building a development/patch-"
                             "queue branch. Export target changed to '%s' and "
                             "patch-export enabled!" % packaging_branch)
                options.patch_export = True
                if not options.patch_export_rev:
                    options.patch_export_rev = options.export
                options.export = packaging_branch
            else:
                gbp.log.warn("It seems you're building a development/patch-"
                             "queue branch. No corresponding packaging branch "
                             "found. Build may fail!")
        elif options.patch_export and not options.patch_export_rev:
            tree = get_tree(repo, options.export)
            spec = parse_spec(options, repo, treeish=tree)
            pq_branch = pq_branch_name(branch, options, spec.version)
            if repo.has_branch(pq_branch):
                gbp.log.info("Exporting patches from development/patch-queue "
                             "branch '%s'" % pq_branch)
                options.patch_export_rev = pq_branch
    if tree is None:
        tree = get_tree(repo, options.export)
        spec = parse_spec(options, repo, treeish=tree)

    # Return tree-ish object and relative spec path for for exporting packaging
    return tree, spec.specpath

def git_archive_build_orig(repo, spec, output_dir, options):
    """
    Build orig tarball using git-archive

    @param repo: our git repository
    @type repo: L{RpmGitRepository}
    @param spec: spec file of the package
    @type spec: L{SpecFile}
    @param output_dir: where to put the tarball
    @type output_dir: C{Str}
    @param options: the parsed options
    @type options: C{dict} of options
    @return: the tree we built the tarball from
    @rtype: C{str}
    """
    try:
        upstream_tree = get_upstream_tree(repo, spec, options)
        gbp.log.info("%s does not exist, creating from '%s'" % \
                        (spec.orig_src['filename'], upstream_tree))
        if spec.orig_src['compression']:
            gbp.log.debug("Building upstream source archive with compression "\
                          "'%s -%s'" % (spec.orig_src['compression'],
                                        options.comp_level))
        if not git_archive(repo, spec, output_dir, options.tmp_dir,
                           upstream_tree, options.orig_prefix,
                           options.comp_level, options.with_submodules):
            raise GbpError("Cannot create upstream tarball at '%s'" % \
                            output_dir)
        return upstream_tree
    except (GitRepositoryError, GbpError) as err:
        raise GbpAutoGenerateError(str(err))


def export_patches(repo, spec, export_treeish, options):
    """
    Generate patches and update spec file
    """
    try:
        upstream_tree = get_upstream_tree(repo, spec, options)
        update_patch_series(repo, spec, upstream_tree, export_treeish, options)
    except (GitRepositoryError, GbpError) as err:
        raise GbpAutoGenerateError(str(err))


def is_native(repo, options):
    """Determine whether a package is native or non-native"""
    if options.native.is_auto():
        if repo.has_branch(options.upstream_branch):
            return False
        # Check remotes, too
        for remote_branch in repo.get_remote_branches():
            remote, branch = remote_branch.split('/', 1)
            if branch == options.upstream_branch:
                gbp.log.debug("Found upstream branch '%s' from remote '%s'" %
                               (remote, branch))
                return False
        return True

    return options.native.is_on()


def setup_builder(options, builder_args):
    """setup everything to use git-pbuilder"""
    if options.builder.startswith('rpmbuild'):
        if len(builder_args) == 0:
            builder_args.append('-ba')
        builder_args.extend(['--define "_topdir %s"' % os.path.abspath(options.export_dir),
                             '--define "_builddir %%_topdir/%s"' % options.build_dir,
                             '--define "_rpmdir %%_topdir/%s"' % options.rpm_dir,
                             '--define "_sourcedir %%_topdir/%s"' % options.source_dir,
                             '--define "_specdir %%_topdir/%s"' % options.spec_dir,
                             '--define "_srcrpmdir %%_topdir/%s"' % options.srpm_dir,
                             '--define "_buildrootdir %%_topdir/%s"' % options.buildroot_dir])
    elif options.builder.startswith('osc'):
        builder_args.insert(0, 'build')
        options.source_dir = ''
        options.spec_dir = ''


def update_tag_str_fields(fields, tag_format_str, repo, commit_info):
    """Update string format fields for packaging tag"""
    fields['nowtime'] = datetime.now().strftime(RpmPkgPolicy.tag_timestamp_format)

    fields['authortime'] = datetime.fromtimestamp(int(commit_info['author'].date.split()[0])).strftime(RpmPkgPolicy.tag_timestamp_format)
    fields['committime'] = datetime.fromtimestamp(int(commit_info['committer'].date.split()[0])).strftime(RpmPkgPolicy.tag_timestamp_format)
    fields['version'] = RpmPkgPolicy.compose_full_version(fields)

    # Parse tags with incremental numbering
    re_fields = dict(fields,
                     nowtimenum=fields['nowtime'] + ".(?P<nownum>[0-9]+)",
                     authortimenum=fields['authortime'] + ".(?P<authornum>[0-9]+)",
                     committimenum=fields['committime'] + ".(?P<commitnum>[0-9]+)")
    try:
        tag_re = re.compile("^%s$" % (tag_format_str % re_fields))
    except KeyError, err:
        raise GbpError, "Unknown field '%s' in packaging-tag format string" % err

    fields['nowtimenum'] = fields['nowtime'] + ".1"
    fields['authortimenum'] = fields['authortime'] + ".1"
    fields['committimenum'] = fields['committime'] + ".1"
    for t in reversed(repo.get_tags()):
        m = tag_re.match(t)
        if m:
            if 'nownum' in m.groupdict():
                fields['nowtimenum'] = "%s.%s" % (fields['nowtime'], int(m.group('nownum'))+1)
            if 'authornum' in m.groupdict():
                fields['authortimenum'] = "%s.%s" % (fields['authortime'], int(m.group('authornum'))+1)
            if 'commitnum' in m.groupdict():
                fields['committimenum'] = "%s.%s" % (fields['committime'], int(m.group('commitnum'))+1)
            break


def packaging_tag_name(repo, spec, commit_info, options):
    """Compose packaging tag as string"""
    tag_str_fields = dict(spec.version, vendor=options.vendor)
    update_tag_str_fields(tag_str_fields, options.packaging_tag, repo,
                          commit_info)
    return repo.version_to_tag(options.packaging_tag, tag_str_fields)


def create_packaging_tag(repo, tag, commit, version, options):
    """Create a packaging/release Git tag"""
    msg = "%s release %s" % (options.vendor,
                             RpmPkgPolicy.compose_full_version(version))
    repo.create_tag(name=tag, msg=msg, sign=options.sign_tags,
                    keyid=options.keyid, commit=commit)


def disable_hooks(options):
    """Disable all hooks (except for builder)"""
    for hook in ['cleaner', 'postexport', 'prebuild', 'postbuild', 'posttag']:
        if getattr(options, hook):
            gbp.log.info("Disabling '%s' hook" % hook)
            setattr(options, hook, '')


def parse_args(argv, prefix, git_treeish=None):
    """Parse config and command line arguments"""
    args = [ arg for arg in argv[1:] if arg.find('--%s' % prefix) == 0 ]
    builder_args = [ arg for arg in argv[1:] if arg.find('--%s' % prefix) == -1 ]

    # We handle these although they don't have a --git- prefix
    for arg in [ "--help", "-h", "--version" ]:
        if arg in builder_args:
            args.append(arg)

    try:
        parser = GbpOptionParserRpm(command=os.path.basename(argv[0]),
                                    prefix=prefix, git_treeish=git_treeish)
    except ConfigParser.ParsingError, err:
        gbp.log.err(err)
        return None, None, None

    tag_group = GbpOptionGroup(parser, "tag options", "options related to git tag creation")
    branch_group = GbpOptionGroup(parser, "branch options", "branch layout options")
    cmd_group = GbpOptionGroup(parser, "external command options", "how and when to invoke external commands and hooks")
    orig_group = GbpOptionGroup(parser, "orig tarball options", "options related to the creation of the orig tarball")
    export_group = GbpOptionGroup(parser, "export build-tree options", "alternative build tree related options")
    parser.add_option_group(tag_group)
    parser.add_option_group(orig_group)
    parser.add_option_group(branch_group)
    parser.add_option_group(cmd_group)
    parser.add_option_group(export_group)

    parser.add_boolean_config_file_option(option_name = "ignore-untracked", dest="ignore_untracked")
    parser.add_boolean_config_file_option(option_name = "ignore-new", dest="ignore_new")
    parser.add_option("--git-verbose", action="store_true", dest="verbose", default=False,
                      help="verbose command execution")
    parser.add_config_file_option(option_name="tmp-dir", dest="tmp_dir")
    parser.add_config_file_option(option_name="color", dest="color", type='tristate')
    parser.add_config_file_option(option_name="color-scheme",
                                  dest="color_scheme")
    parser.add_config_file_option(option_name="notify", dest="notify", type='tristate')
    parser.add_config_file_option(option_name="vendor", action="store", dest="vendor")
    parser.add_config_file_option(option_name="native", dest="native",
                                  type='tristate')
    tag_group.add_option("--git-tag", action="store_true", dest="tag", default=False,
                      help="create a tag after a successful build")
    tag_group.add_option("--git-tag-only", action="store_true", dest="tag_only", default=False,
                      help="don't build, only tag and run the posttag hook")
    tag_group.add_option("--git-retag", action="store_true", dest="retag", default=False,
                      help="don't fail if the tag already exists")
    tag_group.add_boolean_config_file_option(option_name="sign-tags", dest="sign_tags")
    tag_group.add_config_file_option(option_name="keyid", dest="keyid")
    tag_group.add_config_file_option(option_name="packaging-tag", dest="packaging_tag")
    tag_group.add_config_file_option(option_name="upstream-tag", dest="upstream_tag")
    orig_group.add_config_file_option(option_name="upstream-tree", dest="upstream_tree")
    orig_group.add_boolean_config_file_option(option_name="pristine-tar", dest="pristine_tar")
    orig_group.add_boolean_config_file_option(option_name="pristine-tar-commit",
                                              dest="pristine_tar_commit")
    orig_group.add_config_file_option(option_name="force-create", dest="force_create",
                      help="force creation of upstream source tarball", action="store_true")
    orig_group.add_config_file_option(option_name="no-create-orig", dest="no_create_orig",
                      help="don't create upstream source tarball", action="store_true")
    orig_group.add_config_file_option(option_name="tarball-dir", dest="tarball_dir", type="path",
                      help="location to look for external tarballs")
    orig_group.add_config_file_option(option_name="compression-level", dest="comp_level",
                      help="Compression level, default is '%(compression-level)s'")
    orig_group.add_config_file_option(option_name="orig-prefix", dest="orig_prefix")
    branch_group.add_config_file_option(option_name="upstream-branch", dest="upstream_branch")
    branch_group.add_config_file_option(option_name="packaging-branch", dest="packaging_branch")
    branch_group.add_config_file_option(option_name="pq-branch", dest="pq_branch")
    branch_group.add_boolean_config_file_option(option_name = "ignore-branch", dest="ignore_branch")
    branch_group.add_boolean_config_file_option(option_name = "submodules", dest="with_submodules")
    cmd_group.add_config_file_option(option_name="builder", dest="builder",
                      help="command to build the package, default is '%(builder)s'")
    cmd_group.add_config_file_option(option_name="cleaner", dest="cleaner",
                      help="command to clean the working copy, default is '%(cleaner)s'")
    cmd_group.add_config_file_option(option_name="prebuild", dest="prebuild",
                      help="command to run before a build, default is '%(prebuild)s'")
    cmd_group.add_config_file_option(option_name="postexport", dest="postexport",
                      help="command to run after exporting the source tree, default is '%(postexport)s'")
    cmd_group.add_config_file_option(option_name="postbuild", dest="postbuild",
                      help="hook run after a successful build, default is '%(postbuild)s'")
    cmd_group.add_config_file_option(option_name="posttag", dest="posttag",
                      help="hook run after a successful tag operation, default is '%(posttag)s'")
    cmd_group.add_boolean_config_file_option(option_name="hooks", dest="hooks")
    export_group.add_option("--git-no-build", action="store_true",
                      dest="no_build",
                      help="Don't run builder or the associated hooks")
    export_group.add_config_file_option(option_name="export-dir", dest="export_dir", type="path",
                      help="Build topdir, also export the sources under EXPORT_DIR, default is '%(export-dir)s'")
    export_group.add_config_file_option(option_name="rpmbuild-builddir", dest="build_dir", type="path",
                      help="subdir where package is built (under EXPORT_DIR), i.e. rpmbuild builddir, default is '%(rpmbuild-builddir)s'")
    export_group.add_config_file_option(option_name="rpmbuild-rpmdir", dest="rpm_dir", type="path",
                      help="subdir where ready binary packages are stored (under EXPORT_DIR), i.e. rpmbuild builddir, default is '%(rpmbuild-rpmdir)s'")
    export_group.add_config_file_option(option_name="rpmbuild-sourcedir", dest="source_dir", type="path",
                      help="subdir where package sources are stored (under EXPORT_DIR), i.e. rpmbuild sourcedir, default is '%(rpmbuild-sourcedir)s'")
    export_group.add_config_file_option(option_name="rpmbuild-specdir", dest="spec_dir", type="path",
                      help="subdir where package spec file is stored (under EXPORT_DIR), i.e. rpmbuild specdir, default is '%(rpmbuild-specdir)s'")
    export_group.add_config_file_option(option_name="rpmbuild-srpmdir", dest="srpm_dir", type="path",
                      help="subdir where ready sources package is stored (under EXPORT_DIR), i.e. rpmbuild srpmdir, default is '%(rpmbuild-srpmdir)s'")
    export_group.add_config_file_option(option_name="rpmbuild-buildrootdir", dest="buildroot_dir", type="path",
                      help="subdir for build-time alternative root (under EXPORT_DIR), i.e. rpmbuild buildrootdir, default is '%(rpmbuild-buildrootdir)s'")
    export_group.add_config_file_option("export", dest="export",
                      help="export treeish object TREEISH, default is '%(export)s'", metavar="TREEISH")
    export_group.add_config_file_option(option_name="packaging-dir",
                      dest="packaging_dir")
    export_group.add_config_file_option(option_name="spec-file", dest="spec_file")
    export_group.add_option("--git-export-only", action="store_true", dest="export_only", default=False,
                      help="only export packaging files, don't build")
    export_group.add_boolean_config_file_option("patch-export", dest="patch_export")
    export_group.add_option("--git-patch-export-rev", dest="patch_export_rev",
                      metavar="TREEISH",
                      help="[experimental] Export patches from treeish object "
                           "TREEISH")
    export_group.add_config_file_option("patch-export-ignore-path",
                                        dest="patch_export_ignore_path")
    export_group.add_config_file_option("patch-export-compress", dest="patch_export_compress")
    export_group.add_config_file_option("patch-export-squash-until", dest="patch_export_squash_until")
    export_group.add_boolean_config_file_option(option_name="patch-numbers", dest="patch_numbers")
    export_group.add_config_file_option("spec-vcs-tag", dest="spec_vcs_tag")
    options, args = parser.parse_args(args)

    options.patch_export_compress = rpm.string_to_int(options.patch_export_compress)

    gbp.log.setup(options.color, options.verbose, options.color_scheme)
    if not options.hooks:
        disable_hooks(options)
    if options.retag:
        if not options.tag and not options.tag_only:
            gbp.log.err("'--%sretag' needs either '--%stag' or '--%stag-only'" % (prefix, prefix, prefix))
            return None, None, None
    # Use git_treeish as a way to print the warning only on the second parsing
    # round
    if options.export_only and git_treeish:
        gbp.log.warn("Deprecated option '--git-export-only', please use "
                     "'--no-build' instead!")
        options.no_build = True

    return options, args, builder_args


def main(argv):
    """Entry point for git-buildpackage-rpm"""
    retval = 0
    prefix = "git-"
    spec = None

    options, gbp_args, builder_args = parse_args(argv, prefix)
    if not options:
        return 1

    try:
        repo = RpmGitRepository(os.path.curdir)
    except GitRepositoryError:
        gbp.log.err("%s is not a git repository" % (os.path.abspath('.')))
        return 1

    # Determine tree-ish to be exported
    try:
        tree = get_tree(repo, options.export)
    except GbpError as err:
        gbp.log.err('Failed to determine export treeish: %s' % err)
        return 1
    # Re-parse config options with using the per-tree config file(s) from the
    # exported tree-ish
    options, gbp_args, builder_args = parse_args(argv, prefix, tree)

    try:
        # Create base temporary directory for this run
        options.tmp_dir = tempfile.mkdtemp(dir=options.tmp_dir,
                                           prefix='buildpackage-rpm_')
    except GbpError, err:
        gbp.log.err(err)
        return 1

    branch = get_current_branch(repo)

    try:
        tree, relative_spec_path = guess_export_params(repo, options)

        Command(options.cleaner, shell=True)()
        if not options.ignore_new:
            (ret, out) = repo.is_clean(options.ignore_untracked)
            if not ret:
                gbp.log.err("You have uncommitted changes in your source tree:")
                gbp.log.err(out)
                raise GbpError, "Use --git-ignore-new or --git-ignore-untracked to ignore."

        if not options.ignore_new and not options.ignore_branch:
            if branch != options.packaging_branch:
                gbp.log.err("You are not on branch '%s' but on '%s'" % (options.packaging_branch, branch))
                raise GbpError, "Use --git-ignore-branch to ignore or --git-packaging-branch to set the branch name."

        # Dump from git to a temporary directory:
        dump_dir = tempfile.mkdtemp(dir=options.tmp_dir,
                                    prefix='dump_tree_')
        gbp.log.debug("Dumping tree '%s' to '%s'" % (options.export, dump_dir))
        if not dump_tree(repo, dump_dir, tree, options.with_submodules):
            raise GbpError
        # Parse spec from dump dir to get version etc.
        spec = rpm.SpecFile(os.path.join(dump_dir, relative_spec_path))

        if not options.tag_only:
            # Setup builder opts
            setup_builder(options, builder_args)

            # Generate patches, if requested
            if options.patch_export and not is_native(repo, options):
                if options.patch_export_rev:
                    patch_tree = get_tree(repo, options.patch_export_rev)
                else:
                    patch_tree = tree
                export_patches(repo, spec, patch_tree, options)

            # Prepare final export dirs
            export_dir = makedir(options.export_dir)
            source_dir = makedir(os.path.join(export_dir, options.source_dir))
            spec_dir = makedir(os.path.join(export_dir, options.spec_dir))

            # Move packaging files
            gbp.log.debug("Exporting packaging files in '%s' to '%s'" % (spec.specdir, export_dir))
            pkgfiles = os.listdir(spec.specdir)
            for f in pkgfiles:
                src = os.path.join(spec.specdir, f)
                if f == spec.specfile:
                    dst = os.path.join(spec_dir, f)
                else:
                    dst = os.path.join(source_dir, f)
                if not os.path.isdir(src):
                    try:
                        shutil.copy2(src, dst)
                    except IOError as err:
                        raise GbpError, "Error exporting files: %s" % err
            spec.specdir = os.path.abspath(spec_dir)

            if options.orig_prefix != 'auto':
                try:
                    options.orig_prefix %= dict(spec.version,
                        version=RpmPkgPolicy.compose_full_version(spec.version),
                        name=spec.name, vendor=options.vendor)
                except KeyError as err:
                    raise GbpError("Unknown key %s in orig prefix format "
                                   "string" % err)
            elif spec.orig_src:
                options.orig_prefix = spec.orig_src['prefix']

            # Get/build the orig tarball
            if is_native(repo, options):
                if spec.orig_src:
                    # Just build source archive from the exported tree
                    gbp.log.info("Creating (native) source archive %s from '%s'" % (spec.orig_src['filename'], tree))
                    if spec.orig_src['compression']:
                        gbp.log.debug("Building source archive with compression '%s -%s'" % (spec.orig_src['compression'], options.comp_level))
                    if not git_archive(repo, spec, source_dir, options.tmp_dir,
                                       tree, options.orig_prefix,
                                       options.comp_level,
                                       options.with_submodules):
                        raise GbpError("Cannot create source tarball at '%s'" %
                                        source_dir)
            # Non-native packages: create orig tarball from upstream
            elif spec.orig_src:
                 prepare_upstream_tarball(repo, spec, options, source_dir)

            # Run postexport hook
            if options.postexport:
                RunAtCommand(options.postexport, shell=True,
                             extra_env={'GBP_GIT_DIR': repo.git_dir,
                                        'GBP_TMP_DIR': export_dir})(dir=export_dir)
            # Do actual build
            if not options.no_build and not options.tag_only:
                if options.prebuild:
                    RunAtCommand(options.prebuild, shell=True,
                                 extra_env={'GBP_GIT_DIR': repo.git_dir,
                                            'GBP_BUILD_DIR': export_dir})(dir=export_dir)

                # Finally build the package:
                if options.builder.startswith("rpmbuild"):
                    builder_args.append(os.path.join(spec.specdir,
                                        spec.specfile))
                else:
                    builder_args.append(spec.specfile)
                RunAtCommand(options.builder, builder_args, shell=True,
                             extra_env={'GBP_BUILD_DIR': export_dir})(dir=export_dir)
                if options.postbuild:
                    changes = os.path.abspath("%s/%s.changes" % (source_dir, spec.name))
                    gbp.log.debug("Looking for changes file %s" % changes)
                    Command(options.postbuild, shell=True,
                            extra_env={'GBP_CHANGES_FILE': changes,
                                       'GBP_BUILD_DIR': export_dir})()

        # Tag (note: tags the exported version)
        if options.tag or options.tag_only:
            gbp.log.info("Tagging %s" % RpmPkgPolicy.compose_full_version(spec.version))
            commit_info = repo.get_commit_info(tree)
            tag = packaging_tag_name(repo, spec, commit_info, options)
            if options.retag and repo.has_tag(tag):
                repo.delete_tag(tag)
            create_packaging_tag(repo, tag, commit=tree, version=spec.version,
                                 options=options)
            vcs_info = get_vcs_info(repo, tag)
            if options.posttag:
                sha = repo.rev_parse("%s^{}" % tag)
                Command(options.posttag, shell=True,
                        extra_env={'GBP_TAG': tag,
                                   'GBP_BRANCH': branch,
                                   'GBP_SHA1': sha})()
        else:
            vcs_info = get_vcs_info(repo, tree)
        # Put 'VCS:' tag to .spec
        try:
            spec.set_tag('VCS', None, options.spec_vcs_tag % vcs_info)
        except KeyError as err:
            raise GbpError("Unknown key %s in vcs tag format string" % err)
        spec.write_spec_file()

    except CommandExecFailed:
        retval = 1
    except GitRepositoryError as err:
        gbp.log.err("Git command failed: %s" % err)
        retval = 1
    except GbpAutoGenerateError as err:
        if len(err.__str__()):
            gbp.log.err(err)
        retval = 2
    except GbpError, err:
        if len(err.__str__()):
            gbp.log.err(err)
        retval = 1
    finally:
        drop_index(repo)
        shutil.rmtree(options.tmp_dir)

    if not options.tag_only:
        if spec and options.notify:
            summary = "Gbp-rpm %s" % ["failed", "successful"][not retval]
            pkg_evr = {'upstreamversion': spec.version}
            message = ("Build of %s %s %s" % (spec.name,
                            RpmPkgPolicy.compose_full_version(spec.version),
                            ["failed", "succeeded"][not retval]))
            if not gbp.notifications.notify(summary, message, options.notify):
                gbp.log.err("Failed to send notification")
                retval = 1

    return retval

if __name__ == '__main__':
    sys.exit(main(sys.argv))

# vim:et:ts=4:sw=4:et:sts=4:ai:set list listchars=tab\:»·,trail\:·:
