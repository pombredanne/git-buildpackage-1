# vim: set fileencoding=utf-8 :
#
# (C) 2006,2007 Guido Guenther <agx@sigxcpu.org>
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
"""Common functionality of the Debian/RPM package helpers"""

import os
import re
import glob
import stat
import subprocess
import zipfile

import gbp.command_wrappers as gbpc
from gbp.errors import GbpError

# compression types, extra options and extensions
compressor_opts = { 'gzip'  : [ ['-n'], 'gz' ],
                    'bzip2' : [ [], 'bz2' ],
                    'lzma'  : [ [], 'lzma' ],
                    'xz'    : [ [], 'xz' ] }

# Map frequently used names of compression types to the internal ones:
compressor_aliases = { 'bz2' : 'bzip2',
                       'gz'  : 'gzip', }

# Supported archive formats
arhive_formats = [ 'tar', 'zip' ]

# Map combined file extensions to arhive and compression format
archive_ext_aliases = { 'tgz'   : ('tar', 'gzip'),
                        'tbz2'  : ('tar', 'bzip2'),
                        'tlz'   : ('tar', 'lzma'),
                        'txz'   : ('tar', 'xz')}

def parse_archive_filename(filename):
    """
    Given an filename return the basename (i.e. filename without the
    archive and compression extensions), archive format and compression
    method used.

    @param filename: the name of the file
    @type filename: string
    @return: tuple containing basename, archive format and compression method
    @rtype: C{tuple} of C{str}

    >>> parse_archive_filename("abc.tar.gz")
    ('abc', 'tar', 'gzip')
    >>> parse_archive_filename("abc.tar.bz2")
    ('abc', 'tar', 'bzip2')
    >>> parse_archive_filename("abc.def.tbz2")
    ('abc.def', 'tar', 'bzip2')
    >>> parse_archive_filename("abc.def.tar.xz")
    ('abc.def', 'tar', 'xz')
    >>> parse_archive_filename("abc.zip")
    ('abc', 'zip', None)
    >>> parse_archive_filename("abc.lzma")
    ('abc', None, 'lzma')
    >>> parse_archive_filename("abc.tar.foo")
    ('abc.tar.foo', None, None)
    >>> parse_archive_filename("abc")
    ('abc', None, None)
    """
    (base_name, archive_fmt, compression) = (filename, None, None)

    # Split filename to pieces
    split = filename.split(".")
    if len(split) > 1:
        if split[-1] in archive_ext_aliases:
            base_name = ".".join(split[:-1])
            (archive_fmt, compression) = archive_ext_aliases[split[-1]]
        elif split[-1] in arhive_formats:
            base_name = ".".join(split[:-1])
            (archive_fmt, compression) = (split[-1], None)
        else:
            for (c, o) in compressor_opts.iteritems():
                if o[1] == split[-1]:
                    base_name = ".".join(split[:-1])
                    compression = c
                    if len(split) > 2 and split[-2] in arhive_formats:
                        base_name = ".".join(split[:-2])
                        archive_fmt = split[-2]

    return (base_name, archive_fmt, compression)


class PkgPolicy(object):
    """
    Common helpers for packaging policy.
    """
    packagename_re = None
    packagename_msg = None
    upstreamversion_re = None
    upstreamversion_msg = None

    @classmethod
    def is_valid_packagename(cls, name):
        """
        Is this a valid package name?

        >>> PkgPolicy.is_valid_packagename('doesnotmatter')
        Traceback (most recent call last):
        ...
        NotImplementedError: Class needs to provide packagename_re
        """
        if cls.packagename_re is None:
            raise NotImplementedError("Class needs to provide packagename_re")
        return True if cls.packagename_re.match(name) else False

    @classmethod
    def is_valid_upstreamversion(cls, version):
        """
        Is this a valid upstream version number?

        >>> PkgPolicy.is_valid_upstreamversion('doesnotmatter')
        Traceback (most recent call last):
        ...
        NotImplementedError: Class needs to provide upstreamversion_re
        """
        if cls.upstreamversion_re is None:
            raise NotImplementedError("Class needs to provide upstreamversion_re")
        return True if cls.upstreamversion_re.match(version) else False

    @classmethod
    def is_valid_orig_archive(cls, filename):
        "Is this a valid orig source archive"
        (base, arch_fmt, compression) =  parse_archive_filename(filename)
        if arch_fmt == 'tar' and compression:
            return True
        return False

    @classmethod
    def guess_upstream_src_version(cls, filename, extra_regex=r''):
        """
        Guess the package name and version from the filename of an upstream
        archive.

        @param filename: filename (archive or directory) from which to guess
        @type filename: C{string}
        @param extra_regex: additional regex to apply, needs a 'package' and a
                            'version' group
        @return: (package name, version) or ('', '')
        @rtype: tuple

        >>> PkgPolicy.guess_upstream_src_version('foo-bar_0.2.orig.tar.gz')
        ('foo-bar', '0.2')
        >>> PkgPolicy.guess_upstream_src_version('foo-Bar_0.2.orig.tar.gz')
        ('foo-Bar', '0.2.orig')
        >>> PkgPolicy.guess_upstream_src_version('git-bar-0.2.tar.gz')
        ('git-bar', '0.2')
        >>> PkgPolicy.guess_upstream_src_version('git-bar-0.2-rc1.tar.gz')
        ('git-bar', '0.2-rc1')
        >>> PkgPolicy.guess_upstream_src_version('git-bar-0.2:~-rc1.tar.gz')
        ('git-bar', '0.2:~-rc1')
        >>> PkgPolicy.guess_upstream_src_version('git-Bar-0A2d:rc1.tar.bz2')
        ('git-Bar', '0A2d:rc1')
        >>> PkgPolicy.guess_upstream_src_version('git-1.tar.bz2')
        ('git', '1')
        >>> PkgPolicy.guess_upstream_src_version('kvm_87+dfsg.orig.tar.gz')
        ('kvm', '87+dfsg')
        >>> PkgPolicy.guess_upstream_src_version('foo-Bar-a.b.tar.gz')
        ('', '')
        >>> PkgPolicy.guess_upstream_src_version('foo-bar_0.2.orig.tar.xz')
        ('foo-bar', '0.2')
        >>> PkgPolicy.guess_upstream_src_version('foo-bar_0.2.tar.gz')
        ('foo-bar', '0.2')
        >>> PkgPolicy.guess_upstream_src_version('foo-bar_0.2.orig.tar.lzma')
        ('foo-bar', '0.2')
        >>> PkgPolicy.guess_upstream_src_version('foo-bar-0.2.zip')
        ('foo-bar', '0.2')
        >>> PkgPolicy.guess_upstream_src_version('foo-bar-0.2.tlz')
        ('foo-bar', '0.2')
        """
        version_chars = r'[a-zA-Z\d\.\~\-\:\+]'
        basename = parse_archive_filename(os.path.basename(filename))[0]

        version_filters = map ( lambda x: x % version_chars,
                           ( # Debian upstream tarball: package_'<version>.orig.tar.gz'
                             r'^(?P<package>[a-z\d\.\+\-]+)_(?P<version>%s+)\.orig',
                             # Upstream 'package-<version>.tar.gz'
                             # or Debian native 'package_<version>.tar.gz'
                             # or directory 'package-<version>':
                             r'^(?P<package>[a-zA-Z\d\.\+\-]+)(-|_)(?P<version>[0-9]%s*)'))
        if extra_regex:
            version_filters = extra_regex + version_filters

        for filter in version_filters:
            m = re.match(filter, basename)
            if m:
                return (m.group('package'), m.group('version'))
        return ('', '')

    @staticmethod
    def has_orig(orig_file, dir):
        "Check if orig tarball exists in dir"
        try:
            os.stat( os.path.join(dir, orig_file) )
        except OSError:
            return False
        return True

    @staticmethod
    def symlink_orig(orig_file, orig_dir, output_dir, force=False):
        """
        symlink orig tarball from orig_dir to output_dir
        @return: True if link was created or src == dst
                 False in case of error or src doesn't exist
        """
        orig_dir = os.path.abspath(orig_dir)
        output_dir = os.path.abspath(output_dir)

        if orig_dir == output_dir:
            return True

        src = os.path.join(orig_dir, orig_file)
        dst = os.path.join(output_dir, orig_file)
        if not os.access(src, os.F_OK):
            return False
        try:
            if os.access(dst, os.F_OK) and force:
                os.unlink(dst)
            os.symlink(src, dst)
        except OSError:
            return False
        return True


class UpstreamSource(object):
    """
    Upstream source. Can be either an unpacked dir, a tarball or another type
    of archive

    @cvar _orig: are the upstream sources already suitable as an upstream
                 tarball
    @type _orig: boolean
    @cvar _path: path to the upstream sources
    @type _path: string
    @cvar _unpacked: path to the unpacked source tree
    @type _unpacked: string
    """
    def __init__(self, name, unpacked=None, pkg_policy=PkgPolicy, prefix=None):
        self._orig = False
        self._tarball = False
        self._pkg_policy = pkg_policy
        self._path = os.path.abspath(name)
        if not os.path.exists(self._path):
            raise GbpError('UpstreamSource: unable to find %s' % self._path)
        self.unpacked = unpacked
        self._filename_base, \
        self._archive_fmt, \
        self._compression = parse_archive_filename(os.path.basename(self.path))
        self._prefix = prefix
        if self._prefix is None:
            self._determine_prefix()

        self._check_orig()
        if self.is_dir():
            self.unpacked = self.path

    def _check_orig(self):
        """
        Check if upstream source format can be used as orig tarball.
        This doesn't imply that the tarball is correctly named.

        @return: C{True} if upstream source format is suitable
            as upstream tarball, C{False} otherwise.
        @rtype: C{bool}
        """
        if self.is_dir():
            self._orig = False
            self._tarball = False
            return

        self._tarball = True if self.archive_fmt == 'tar' else False
        self._orig = self._pkg_policy.is_valid_orig_archive(os.path.basename(self.path))

    def is_orig(self):
        """
        @return: C{True} if sources are suitable as upstream source,
            C{False} otherwise
        @rtype: C{bool}
        """
        return self._orig

    def is_tarball(self):
        """
        @return: C{True} if source is a tarball, C{False} otherwise
        @rtype: C{bool}
        """
        return self._tarball

    def is_dir(self):
        """
        @return: C{True} if if upstream sources are an unpacked directory,
            C{False} otherwise
        @rtype: C{bool}
        """
        return True if os.path.isdir(self._path) else False

    @property
    def path(self):
        return self._path.rstrip('/')


    @staticmethod
    def _get_topdir_files(file_list):
        """Parse content of the top directory from a file list

        >>> UpstreamSource._get_topdir_files([])
        set([])
        >>> UpstreamSource._get_topdir_files([('-', 'foo/bar')])
        set([('d', 'foo')])
        >>> UpstreamSource._get_topdir_files([('d', 'foo/'), ('-', 'foo/bar')])
        set([('d', 'foo')])
        >>> UpstreamSource._get_topdir_files([('d', 'foo'), ('-', 'foo/bar')])
        set([('d', 'foo')])
        >>> UpstreamSource._get_topdir_files([('-', 'fob'), ('d', 'foo'), ('d', 'foo/bar'), ('-', 'foo/bar/baz')])
        set([('-', 'fob'), ('d', 'foo')])
        """
        topdir_files = set()
        for typ, path in file_list:
            split = path.lstrip('/').split('/')
            if len(split) == 1:
                topdir_files.add((typ, path))
            else:
                topdir_files.add(('d', split[0]))
        return topdir_files

    def _determine_prefix(self):
        """Determine the prefix, i.e. the "leading directory name"""
        self._prefix = ''
        if self.is_dir():
            # For directories we presume that the prefix is just the dirname
            self._prefix = os.path.basename(self.path.rstrip('/'))
        else:
            files = []
            if self._archive_fmt == 'zip':
                archive = zipfile.ZipFile(self.path)
                for info in archive.infolist():
                    typ = 'd' if stat.S_ISDIR(info.external_attr >> 16) else '?'
                    files.append((typ, info.filename))
            elif self._archive_fmt == 'tar':
                popen = subprocess.Popen(['tar', '-t', '-v', '-f', self.path],
                                         stdout=subprocess.PIPE)
                out, _err = popen.communicate()
                if popen.returncode:
                    raise GbpError("Listing tar archive content failed")
                for line in out.splitlines():
                    fields = line.split(None, 5)
                    files.append((fields[0][0], fields[-1]))
            else:
                raise GbpError("Unsupported archive format %s, unable to "
                               "determine prefix for '%s'" %
                               (self._archive_fmt, self.path))
            # Determine prefix from the archive content
            topdir_files = self._get_topdir_files(files)
            if len(topdir_files) == 1:
                typ, name = topdir_files.pop()
                if typ == 'd':
                    self._prefix = name

    @property
    def archive_fmt(self):
        """Archive format of the sources, e.g. 'tar'"""
        return self._archive_fmt

    @property
    def compression(self):
        """Compression format of the sources, e.g. 'gzip'"""
        return self._compression

    @property
    def prefix(self):
        """Prefix, i.e. the 'leading directory name' of the sources"""
        return self._prefix

    def unpack(self, dir, filters=[]):
        """
        Unpack packed upstream sources into a given directory
        and determine the toplevel of the source tree.
        """
        if self.is_dir():
            raise GbpError("Cannot unpack directory %s" % self.path)

        if not filters:
            filters = []

        if type(filters) != type([]):
            raise GbpError("Filters must be a list")

        if self._unpack_archive(dir, filters):
            ret = type(self)(dir, prefix=self._prefix)
        else:
            ret = self
        src_dir = os.path.join(dir, self._prefix)
        ret.unpacked = src_dir if os.path.isdir(src_dir) else dir
        return ret

    def _unpack_archive(self, dir, filters):
        """
        Unpack packed upstream sources into a given directory. Return True if
        the output was filtered, otherwise False.
        """
        ext = os.path.splitext(self.path)[1]
        if ext in [ ".zip", ".xpi" ]:
            self._unpack_zip(dir)
        else:
            self._unpack_tar(dir, filters)
            if filters:
                return True
        return False

    def _unpack_zip(self, dir):
        try:
            gbpc.UnpackZipArchive(self.path, dir)()
        except gbpc.CommandExecFailed:
            raise GbpError("Unpacking of %s failed" % self.path)

    def _unpack_tar(self, dir, filters):
        """
        Unpack a tarball to I{dir} applying a list of I{filters}. Leave the
        cleanup to the caller in case of an error.
        """
        try:
            unpackArchive = gbpc.UnpackTarArchive(self.path, dir, filters)
            unpackArchive()
        except gbpc.CommandExecFailed:
            # unpackArchive already printed an error message
            raise GbpError

    def pack(self, newarchive, filters=[], newprefix=None):
        """
        Recreate a new archive from the current one

        @param newarchive: the name of the new archive
        @type newarchive: string
        @param filters: tar filters to apply
        @type filters: array of strings
        @param newprefix: new prefix, None implies that prefix is not mangled
        @type newprefix: string or None
        @return: the new upstream source
        @rtype: UpstreamSource
        """
        if not self.unpacked:
            raise GbpError("Need an unpacked source tree to pack")

        if not filters:
            filters = []

        if type(filters) != type([]):
            raise GbpError("Filters must be a list")

        run_dir = os.path.dirname(self.unpacked.rstrip('/'))
        pack_this = os.path.basename(self.unpacked.rstrip('/'))
        transform = None
        if newprefix is not None:
            newprefix = newprefix.strip('/.')
            if newprefix:
                transform = 's!%s!%s!' % (pack_this, newprefix)
            else:
                transform = 's!%s!%s!' % (pack_this, '.')
        try:
            repackArchive = gbpc.PackTarArchive(newarchive,
                                                run_dir,
                                                pack_this,
                                                filters,
                                                transform=transform)
            repackArchive()
        except gbpc.CommandExecFailed:
            # repackArchive already printed an error
            raise GbpError
        new = type(self)(newarchive)
        # Reuse the same unpacked dir if the content matches
        if not filters:
            new.unpacked = self.unpacked
        return new

    @staticmethod
    def known_compressions():
        return [ args[1][-1] for args in compressor_opts.items() ]

    def guess_version(self, extra_regex=r''):
        return self._pkg_policy.guess_upstream_src_version(self.path,
                                                           extra_regex)
