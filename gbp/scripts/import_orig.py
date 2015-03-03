# vim: set fileencoding=utf-8 :
#
# (C) 2006, 2007, 2009, 2011, 2015 Guido Guenther <agx@sigxcpu.org>
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
"""Import a new upstream version into a Git repository"""

from six.moves import configparser
import os
import sys
import tempfile
import gbp.command_wrappers as gbpc
from gbp.deb import (DebianPkgPolicy, parse_changelog_repo)
from gbp.deb.upstreamsource import DebianUpstreamSource
from gbp.deb.uscan import (Uscan, UscanError)
from gbp.deb.changelog import ChangeLog, NoChangeLogError
from gbp.deb.git import (GitRepositoryError, DebianGitRepository)
from gbp.config import GbpOptionParserDebian, GbpOptionGroup, no_upstream_branch_msg
from gbp.errors import GbpError
from gbp.format import format_str
import gbp.log
from gbp.scripts.common.import_orig import (orig_needs_repack, cleanup_tmp_tree,
                                            ask_package_name, ask_package_version,
                                            repack_source, is_link_target, download_orig)


def prepare_pristine_tar(archive, pkg, version):
    """
    Prepare the upstream source for pristine tar import.

    This checks if the upstream source is actually a tarball
    and creates a symlink from I{archive}
    to I{<pkg>_<version>.orig.tar.<ext>} so pristine-tar will
    see the correct basename.

    @param archive: the upstream source's name
    @type archive: C{str}
    @param pkg: the source package's name
    @type pkg: C{str}
    @param version: the upstream version number
    @type version: C{str}
    @rtype: C{str}
    """
    linked = False
    if os.path.isdir(archive):
        return None

    ext = os.path.splitext(archive)[1]
    if ext in ['.tgz', '.tbz2', '.tlz', '.txz' ]:
        ext = ".%s" % ext[2:]

    link = "../%s_%s.orig.tar%s" % (pkg, version, ext)

    if os.path.basename(archive) != os.path.basename(link):
        try:
            if not is_link_target(archive, link):
                os.symlink(os.path.abspath(archive), link)
                linked = True
        except OSError as err:
                raise GbpError("Cannot symlink '%s' to '%s': %s" % (archive, link, err[1]))
        return (link, linked)
    else:
        return (archive, linked)


def upstream_import_commit_msg(options, version):
    return options.import_msg % dict(version=version)


def detect_name_and_version(repo, source, options):
    # Guess defaults for the package name and version from the
    # original tarball.
    guessed_package, guessed_version = source.guess_version()

    # Try to find the source package name
    try:
        cp = ChangeLog(filename='debian/changelog')
        sourcepackage = cp['Source']
    except NoChangeLogError:
        try:
            # Check the changelog file from the repository, in case
            # we're not on the debian-branch (but upstream, for
            # example).
            cp = parse_changelog_repo(repo, options.debian_branch, 'debian/changelog')
            sourcepackage = cp['Source']
        except NoChangeLogError:
            if options.interactive:
                sourcepackage = ask_package_name(guessed_package,
                                                 DebianPkgPolicy.is_valid_packagename,
                                                 DebianPkgPolicy.packagename_msg)
            else:
                if guessed_package:
                    sourcepackage = guessed_package
                else:
                    raise GbpError("Couldn't determine upstream package name. Use --interactive.")

    # Try to find the version.
    if options.version:
        version = options.version
    else:
        if options.interactive:
            version = ask_package_version(guessed_version,
                                          DebianPkgPolicy.is_valid_upstreamversion,
                                          DebianPkgPolicy.upstreamversion_msg)
        else:
            if guessed_version:
                version = guessed_version
            else:
                raise GbpError("Couldn't determine upstream version. Use '-u<version>' or --interactive.")

    return (sourcepackage, version)


def find_source(use_uscan, args):
    """Find the tarball to import - either via uscan or via command line argument
    @return: upstream source filename or None if nothing to import
    @rtype: string
    @raise GbpError: raised on all detected errors
    """
    if use_uscan:
        if args:
            raise GbpError("you can't pass both --uscan and a filename.")

        uscan = Uscan()
        gbp.log.info("Launching uscan...")
        try:
            uscan.scan()
        except UscanError as e:
            raise GbpError("%s" % e)

        if not uscan.uptodate:
            if uscan.tarball:
                gbp.log.info("using %s" % uscan.tarball)
                args.append(uscan.tarball)
            else:
                raise GbpError("uscan didn't download anything, and no source was found in ../")
        else:
            gbp.log.info("package is up to date, nothing to do.")
            return None
    if len(args) > 1: # source specified
        raise GbpError("More than one archive specified. Try --help.")
    elif len(args) == 0:
        raise GbpError("No archive to import specified. Try --help.")
    else:
        archive = DebianUpstreamSource(args[0])
        return archive


def repacked_tarball_name(source, name, version):
    if source.is_orig():
        # Repacked orig tarball needs a different name since there's already
        # one with that name
        name = os.path.join(
                    os.path.dirname(source.path),
                    os.path.basename(source.path).replace(".tar", ".gbp.tar"))
    else:
        # Repacked sources or other archives get canonical name
        name = os.path.join(
                    os.path.dirname(source.path),
                    "%s_%s.orig.tar.bz2" % (name, version))
    return name


def set_bare_repo_options(options):
    """Modify options for import into a bare repository"""
    if options.pristine_tar or options.merge:
        gbp.log.info("Bare repository: setting %s%s options"
                      % (["", " '--no-pristine-tar'"][options.pristine_tar],
                         ["", " '--no-merge'"][options.merge]))
        options.pristine_tar = False
        options.merge = False


def build_parser(name):
    try:
        parser = GbpOptionParserDebian(command=os.path.basename(name), prefix='',
                                       usage='%prog [options] /path/to/upstream-version.tar.gz | --uscan')
    except configparser.ParsingError as err:
        gbp.log.err(err)
        return None

    import_group = GbpOptionGroup(parser, "import options",
                      "pristine-tar and filtering")
    tag_group = GbpOptionGroup(parser, "tag options",
                      "options related to git tag creation")
    branch_group = GbpOptionGroup(parser, "version and branch naming options",
                      "version number and branch layout options")
    cmd_group = GbpOptionGroup(parser, "external command options", "how and when to invoke external commands and hooks")

    for group in [import_group, branch_group, tag_group, cmd_group ]:
        parser.add_option_group(group)

    branch_group.add_option("-u", "--upstream-version", dest="version",
                      help="Upstream Version")
    branch_group.add_config_file_option(option_name="debian-branch",
                      dest="debian_branch")
    branch_group.add_config_file_option(option_name="upstream-branch",
                      dest="upstream_branch")
    branch_group.add_option("--upstream-vcs-tag", dest="vcs_tag",
                            help="Upstream VCS tag add to the merge commit")
    branch_group.add_boolean_config_file_option(option_name="merge", dest="merge")
    branch_group.add_boolean_config_file_option(
                      option_name="create-missing-branches",
                      dest="create_missing_branches")

    tag_group.add_boolean_config_file_option(option_name="sign-tags",
                      dest="sign_tags")
    tag_group.add_config_file_option(option_name="keyid",
                      dest="keyid")
    tag_group.add_config_file_option(option_name="upstream-tag",
                      dest="upstream_tag")
    import_group.add_config_file_option(option_name="filter",
                      dest="filters", action="append")
    import_group.add_boolean_config_file_option(option_name="pristine-tar",
                      dest="pristine_tar")
    import_group.add_boolean_config_file_option(option_name="filter-pristine-tar",
                      dest="filter_pristine_tar")
    import_group.add_config_file_option(option_name="import-msg",
                      dest="import_msg")
    import_group.add_boolean_config_file_option(option_name="symlink-orig",
                                                dest="symlink_orig")
    cmd_group.add_config_file_option(option_name="postimport", dest="postimport")

    parser.add_boolean_config_file_option(option_name="interactive",
                                          dest='interactive')
    parser.add_option("-v", "--verbose", action="store_true", dest="verbose", default=False,
                      help="verbose command execution")
    parser.add_config_file_option(option_name="color", dest="color", type='tristate')
    parser.add_config_file_option(option_name="color-scheme",
                                  dest="color_scheme")

    # Accepted for compatibility
    parser.add_option("--no-dch", dest='no_dch', action="store_true",
                      default=False, help="deprecated - don't use.")
    parser.add_option("--uscan", dest='uscan', action="store_true",
                      default=False, help="use uscan(1) to download the new tarball.")
    parser.add_option("--download", dest='download', action="store_true",
                      default=False, help="Download from URL via http(s).")
    return parser


def parse_args(argv):
    """Parse the command line arguments
    @return: options and arguments

    # Silence error output
    >>> _gbp_log_error_bak = gbp.log.error
    >>> gbp.log.error = lambda x: None
    >>> parse_args(['arg0', '--download', '--uscan'])
    (None, None)
    >>> parse_args(['arg0', '--download', 'first', 'second'])
    (None, None)
    >>> gbp.log.error = _gbp_log_error_bak
    """

    parser = build_parser(argv[0])
    if not parser:
        return None, None

    (options, args) = parser.parse_args(argv[1:])
    gbp.log.setup(options.color, options.verbose, options.color_scheme)

    if options.no_dch:
        gbp.log.warn("'--no-dch' passed. This is now the default, please remove this option.")

    if options.uscan and options.download:
        gbp.log.error("Either uscan or --download can be used, not both.")
        return None, None

    if options.download and len(args) != 1:
        gbp.log.error("Need exactly one URL to download not %s" % args)
        return None, None

    return options, args


def main(argv):
    ret = 0
    tmpdir = tempfile.mkdtemp(dir='../')
    pristine_orig = None
    linked = False

    gbp.log.initialize()

    (options, args) = parse_args(argv)
    if not options:
        return 1

    try:
        if options.download:
            source = download_orig(args[0])
        else:
            source = find_source(options.uscan, args)
        if not source:
            return ret

        try:
            repo = DebianGitRepository('.')
        except GitRepositoryError:
            raise GbpError("%s is not a git repository" % (os.path.abspath('.')))

        # an empty repo has now branches:
        initial_branch = repo.get_branch()
        is_empty = False if initial_branch else True

        if not repo.has_branch(options.upstream_branch) and not is_empty:
            if options.create_missing_branches:
                gbp.log.info("Will create missing branch '%s'" %
                             options.upstream_branch)
            else:
                raise GbpError(no_upstream_branch_msg % options.upstream_branch)

        (sourcepackage, version) = detect_name_and_version(repo, source, options)

        (clean, out) = repo.is_clean()
        if not clean and not is_empty:
            gbp.log.err("Repository has uncommitted changes, commit these first: ")
            raise GbpError(out)

        if repo.bare:
            set_bare_repo_options(options)

        if not source.is_dir():
            unpack_dir = tempfile.mkdtemp(prefix='unpack', dir=tmpdir)
            source.unpack(unpack_dir, options.filters)
            gbp.log.debug("Unpacked '%s' to '%s'" % (source.path, source.unpacked))

        if orig_needs_repack(source, options):
            gbp.log.debug("Filter pristine-tar: repacking '%s' from '%s'" % (source.path, source.unpacked))
            repack_dir = tempfile.mkdtemp(prefix='repack', dir=tmpdir)
            repack_name = repacked_tarball_name(source, sourcepackage, version)
            source = repack_source(source, repack_name, repack_dir, options.filters)

        (pristine_orig, linked) = prepare_pristine_tar(source.path,
                                                       sourcepackage,
                                                       version)

        # Don't mess up our repo with git metadata from an upstream tarball
        try:
            if os.path.isdir(os.path.join(source.unpacked, '.git/')):
                raise GbpError("The orig tarball contains .git metadata - giving up.")
        except OSError:
            pass

        try:
            upstream_branch = [ options.upstream_branch, 'master' ][is_empty]
            filter_msg = ["", " (filtering out %s)"
                              % options.filters][len(options.filters) > 0]
            gbp.log.info("Importing '%s' to branch '%s'%s..." % (source.path,
                                                                 upstream_branch,
                                                                 filter_msg))
            gbp.log.info("Source package is %s" % sourcepackage)
            gbp.log.info("Upstream version is %s" % version)

            import_branch = [ options.upstream_branch, None ][is_empty]
            msg = upstream_import_commit_msg(options, version)

            if options.vcs_tag:
                parents = [repo.rev_parse("%s^{}" % options.vcs_tag)]
            else:
                parents = None

            commit = repo.commit_dir(source.unpacked,
                        msg=msg,
                        branch=import_branch,
                        other_parents=parents,
                        create_missing_branch=options.create_missing_branches)

            if options.pristine_tar:
                if pristine_orig:
                    repo.pristine_tar.commit(pristine_orig, upstream_branch)
                else:
                    gbp.log.warn("'%s' not an archive, skipping pristine-tar" % source.path)

            tag = repo.version_to_tag(options.upstream_tag, version)
            repo.create_tag(name=tag,
                            msg="Upstream version %s" % version,
                            commit=commit,
                            sign=options.sign_tags,
                            keyid=options.keyid)
            if is_empty:
                repo.create_branch(options.upstream_branch, rev=commit)
                repo.force_head(options.upstream_branch, hard=True)
            elif options.merge:
                gbp.log.info("Merging to '%s'" % options.debian_branch)
                repo.set_branch(options.debian_branch)
                try:
                    repo.merge(tag)
                except GitRepositoryError:
                    raise GbpError("Merge failed, please resolve.")
                if options.postimport:
                    epoch = ''
                    if os.access('debian/changelog', os.R_OK):
                        # No need to check the changelog file from the
                        # repository, since we're certain that we're on
                        # the debian-branch
                        cp = ChangeLog(filename='debian/changelog')
                        if cp.has_epoch():
                            epoch = '%s:' % cp.epoch
                    info = { 'version': "%s%s-1" % (epoch, version) }
                    env = { 'GBP_BRANCH': options.debian_branch }
                    gbpc.Command(format_str(options.postimport, info), extra_env=env, shell=True)()
            # Update working copy and index if we've possibly updated the
            # checked out branch
            current_branch = repo.get_branch()
            if current_branch in [ options.upstream_branch,
                                   repo.pristine_tar_branch]:
                repo.force_head(current_branch, hard=True)
        except (gbpc.CommandExecFailed, GitRepositoryError) as err:
            msg = err.__str__() if len(err.__str__()) else ''
            raise GbpError("Import of %s failed: %s" % (source.path, msg))
    except (GbpError, GitRepositoryError) as err:
        if len(err.__str__()):
            gbp.log.err(err)
        ret = 1

    if pristine_orig and linked and not options.symlink_orig:
        os.unlink(pristine_orig)

    if tmpdir:
        cleanup_tmp_tree(tmpdir)

    if not ret:
        gbp.log.info("Successfully imported version %s of %s" % (version, source.path))
    return ret

if __name__ == "__main__":
    sys.exit(main(sys.argv))

# vim:et:ts=4:sw=4:et:sts=4:ai:set list listchars=tab\:»·,trail\:·:
