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
"""provides some rpm source package related helpers"""

import commands
import sys
import os
import re
import tempfile
import rpm
import glob
import shutil as shutil
from optparse import OptionParser

import gbp.command_wrappers as gbpc
from gbp.errors import GbpError
from gbp.git import GitRepositoryError
from gbp.patch_series import (PatchSeries, Patch)
import gbp.log
from gbp.pkg import (PkgPolicy, UpstreamSource)

# define a large number to check the valid id of source file
MAX_SOURCE_NUMBER = 9999

# When trying to parse a version-number, these are
# the valid characters.
rpm_version_chars = 'a-zA-Z\d.~+'

class RpmPkgPolicy(PkgPolicy):
    """Packaging policy for RPM"""

    # From http://wiki.meego.com/Packaging/Guidelines#Package_Naming
    # "Do NOT use an underscore '_', a plus '+', or a period '.' as a delimiter"
    # Additionally, name must begin with an alphanumeric
    packagename_re = re.compile("^[a-zA-Z0-9][a-zA-Z0-9\-]+$")
    packagename_msg = """Package names must be at least two characters long, start with an
    alphanumeric and can only contain alphanumerics or minus signs (-)"""

    # From http://wiki.meego.com/Packaging/Guidelines#Version_and_Release
    # The upstream_version may contain only alphanumerics and the
    # characters . ~ (full stop, tilde) and# should start with a digit.
    # "We can use letters and tilde into the version tag. We do not use the
    # Release field for this."
    upstreamversion_re = re.compile("^[0-9][a-zA-Z0-9\.\~]*$")
    upstreamversion_msg = """Upstream version numbers must start with a digit and can only containg alphanumerics,
    full stops (.) and tildes (~)"""


class NoSpecError(Exception):
    """no changelog found"""
    pass


class RpmHdrInfo(object):
    """Describes the RPM package header"""
    release_re = re.compile(r'(?P<release>[0-9]*)\.(?P<buildid>[a-zA-Z0-9].*)$')

    def __init__(self, rpmhdr):
        self._hdr = rpmhdr
        self.buildid = ""
        m = self.release_re.match(self[rpm.RPMTAG_RELEASE])
        if m and m.group('buildid'):
            self.buildid = m.group('buildid')

    def __getitem__(self, name):
        return self._hdr[name]


class SrcRpmFile(object):
    """Keeps all needed data read from a source rpm"""
    release_re = re.compile(r'(?P<release>[0-9]*)\.(?P<buildid>[a-zA-Z0-9].*)$')

    def __init__(self, srpmfile):
        # Do not required signed packages to be able to import
        ts_vsflags = (rpm.RPMVSF_NOMD5HEADER | rpm.RPMVSF_NORSAHEADER |
                      rpm.RPMVSF_NOSHA1HEADER | rpm.RPMVSF_NODSAHEADER |
                      rpm.RPMVSF_NOMD5 | rpm.RPMVSF_NORSA | rpm.RPMVSF_NOSHA1 |
                      rpm.RPMVSF_NODSA)
        srpmfp = open(srpmfile)
        rpmhdr = rpm.ts(vsflags=ts_vsflags).hdrFromFdno(srpmfp.fileno())
        srpmfp.close()
        self.rpmhdr = RpmHdrInfo(rpmhdr)
        self.srpmfile = os.path.abspath(srpmfile)
        (self.orig_file, self.orig_format, self.orig_comp) = self.guess_orig_file()

    def _get_version(self):
        """
        Get the (downstream) version of the RPM
        """
        version = [ self.rpmhdr[rpm.RPMTAG_EPOCH] + ":" ] if self.rpmhdr[rpm.RPMTAG_EPOCH] else ""
        version += self.rpmhdr[rpm.RPMTAG_VERSION]+"-"+self.rpmhdr[rpm.RPMTAG_RELEASE]
        return version

    version = property(_get_version)

    def _get_name(self):
        """
        Get the name of the RPM package
        """
        return self.rpmhdr[rpm.RPMTAG_NAME]
    pkg = property(_get_name)

    def _get_upstream_version(self):
        """
        Get the upstream version of the package
        """
        return self.rpmhdr[rpm.RPMTAG_VERSION]
    upstream_version = property(_get_upstream_version)

    def __str__(self):
        return "<%s object %s>" % (self.__class__.__name__, self.srpmfile)


    def unpack(self, dest_dir, srctarballdir=None):
        """
        Unpack the source rpm to tmpdir, move source tarball to srctallbardir.
        Leave the cleanup to the caller in case of an error
        """
        gbpc.RunAtCommand('rpm2cpio',
                          [self.srpmfile, '|', 'cpio', '-id'],
                          shell=True)(dir=dest_dir)

        # Unpack source tarball
        if self.orig_file:
            orig_tarball = os.path.join(dest_dir, self.orig_file)
            if srctarballdir:
                if os.path.isdir(srctarballdir):
                    shutil.move(orig_tarball, srctarballdir)
                else:
                    raise GbpError, "Src tarball destination dir not found or not a directory"
        else:
            gbp.log.warn("Failed to detect source tarball. Import may be incorrect")
            #raise GbpError, "Failed to detect source tarball"

    def guess_orig_file(self):
        """
        Try to guess the name of the primary upstream/source tarball
        returns a tuple with tarball filename and compression suffix
        """
        tarball_re = re.compile(
            r'(?P<name>%s).*?\.(?P<format>tar|zip|tgz)\.?(?P<comp>(bz2|gz|xz|lzma|\b))$' % \
                self.rpmhdr[rpm.RPMTAG_NAME])
        tarball = ""
        comp = ""
        formt = ""

        # Take the first file that starts 'name' and has suffix like 'tar.*'
        for s in self.rpmhdr[rpm.RPMTAG_SOURCE]:
            m = tarball_re.match(os.path.basename(s))
            if m:
                # Take the first tarball that starts with pkg name
                if m.group('name'):
                    tarball = s
                    formt = m.group('format')
                    comp = m.group('comp')
                    break
                # otherwise we take the first tarball
                elif not tarball:
                    tarball = s
                    formt = m.group('format')
                    comp = m.group('comp')
                # else don't accept
        if (formt, comp) == ('tgz', ''):
            formt, comp = 'tar', 'gz'
        return (tarball, formt, comp)


    def debugprint(self):
        """
        Print info about the RPM in readable way
        """
        gbp.log.debug("Package %s" % self.rpmhdr[rpm.RPMTAG_NAME])
        gbp.log.debug("Version: %s" % self.rpmhdr[rpm.RPMTAG_VERSION])
        gbp.log.debug("Release: %s" % self.rpmhdr[rpm.RPMTAG_RELEASE])
        gbp.log.debug("BuildId: %s" % self.rpmhdr.buildid)
#        gbp.log.debug("Source tarball: %s" % srpm.tarball)
        if self.rpmhdr[rpm.RPMTAG_EPOCH]:
            gbp.log.debug("Epoch: %s" % self.rpmhdr[rpm.RPMTAG_EPOCH])


class SpecFile(object):
    """Class for parsing/modifying spec files"""
    source_re = re.compile(r'^Source(?P<srcnum>[0-9]+)?:\s*(?P<filename>[^\s].*[^\s])\s*$', flags=re.I)
    patchfile_re = re.compile(r'^Patch(?P<patchnum>[0-9]+)?:\s*(?P<filename>.+)\s*$', flags=re.I)
    applypatch_re = re.compile(r'^%patch(?P<patchnum>[0-9]+)?(\s+(?P<args>.*))?$')
    marker_re = re.compile(r'^#\s+(?P<marker>>>|<<)\s+(?P<what>gbp-[^\s]+)\s*(?P<comment>.*)$')

    def __init__(self, specfile):
        try:
            self.specinfo = rpm.ts().parseSpec(specfile)
        except ValueError, err:
            raise GbpError, "RPM error while parsing spec: %s" % err

        self.name = self.specinfo.packages[0].header[rpm.RPMTAG_NAME]
        self.version = self.specinfo.packages[0].header[rpm.RPMTAG_VERSION]
        self.release = self.specinfo.packages[0].header[rpm.RPMTAG_RELEASE]
        self.epoch = self.specinfo.packages[0].header[rpm.RPMTAG_EPOCH]
        self.specfile = os.path.abspath(specfile)
        self.specdir = os.path.dirname(self.specfile)
        self.patches = {}
        self.sources = {}
        (self.orig_file, self.orig_base, self.orig_format, self.orig_comp) = self.guess_orig_file()

        patchparser = OptionParser()
        patchparser.add_option("-p", dest="strip")
        patchparser.add_option("-s", dest="silence")
        patchparser.add_option("-P", dest="patchnum")
        patchparser.add_option("-b", dest="backup")
        patchparser.add_option("-E", dest="removeempty")

        # get patches
        for (name, num, typ) in self.specinfo.sources:
            # workaround rpm parsing bug
            if num >= MAX_SOURCE_NUMBER:
                num = 0
            # only add files of patch type
            if typ == 2:
                self.patches[num] = {'filename': name, 'strip': '0', 'apply': False, 'autoupdate': False}
            if typ == 1:
                self.sources[num] = {'filename': name, 'num': num}

        # Parse info from spec file
        f = file(self.specfile)
        autoupdate = False
        for line in f:
            m = self.applypatch_re.match(line)
            if m:
                (options, args) = patchparser.parse_args(m.group('args').split(" \t"))
                if m.group('patchnum'):
                    patchnum = int(m.group('patchnum'))
                elif options.patchnum:
                    patchnum = int(options.patchnum)
                else:
                    patchnum = 0

                if options.strip:
                    self.patches[patchnum]['strip'] = options.strip

                self.patches[patchnum]['apply'] = True
                continue

            # Find patch tags inside autoupdate markers
            m = self.marker_re.match(line)
            if m:
                if m.group('what') == "gbp-patch-tags":
                    if m.group('marker') == '>>':
                        autoupdate = True
                    else:
                        autoupdate = False
                continue
            m = self.patchfile_re.match(line)
            if m:
                if m.group('patchnum'):
                    patchnum = int(m.group('patchnum'))
                else:
                    patchnum = 0
                self.patches[patchnum]['autoupdate'] = autoupdate
                continue

        f.close()

    # RPMTODO: complete this
    def putautoupdatemarkers(self):
        """
        Update spec by putting autoupdate markers
        Returns the number of lines added
        """
        f = file(self.specfile)
        lines = f.readlines()
        f.close()

        patchtags = [0, 0]      # line number of first tag and number of lines
        patchmacros = [0, 0]    # line number of first macro and number of lines
        sourcetag = 0
        prepmacro = 0
        setupmacro = 0

        # Check where patch tags and macros are
        numlines = len(lines)
        for i in range(numlines):
            l = lines[i]

            if self.marker_re.match(l):
                gbp.log.info("gbp autoupdate margers already found, not modifying spec file")
                return 0

            if re.match("^patch[0-9]*:", l, flags=re.I):
                if patchtags[0] == 0:
                    patchtags[0] = i
                patchtags[1] = i - patchtags[0] + 1
                continue
            if re.match("^%patch[0-9]*(\s.*)?", l):
                if patchmacros[0] == 0:
                    patchmacros[0] = i
                patchmacros[1] = i - patchmacros[0] + 1
                continue
            # Only search for the last occurrence of the following
            if re.match("^source[0-9]*:", l, flags=re.I):
                sourcetag = i
                continue
            if re.match("^%setup(\s.*)?$", l):
                setupmacro = i
            if re.match("^%prep(\s.*)?$", l):
                prepmacro = i
                continue

        if patchtags[0] == 0:
            patchtags[0] = sourcetag+1
            patchtags[1] = 0
            gbp.log.info("Didn't find any 'Patch' tags, putting autoupdate markers after the last 'Source' tag.")
        if patchmacros[0] == 0:
            patchmacros[0] = setupmacro+1
            patchmacros[1] = 0
            gbp.log.info("Didn't find any '%patch' macros, putting autoupdate markers after the last '%setup' macro.")

        lines_added = 0
        if patchtags[0]:
            lines.insert(patchtags[0], "# >> gbp-patch-tags         # auto-added by gbp\n")
            lines.insert(patchtags[0]+patchtags[1]+1, "# << gbp-patch-tags         # auto-added by gbp\n")
            lines_added += 2
        else:
            gbp.log.warn("Couldn't determine position where to add gbp-patch-tags autoupdate markers")

        if patchmacros[0]:
            lines.insert(patchmacros[0]+lines_added, "# >> gbp-apply-patches    # auto-added by gbp\n")
            lines.insert(patchmacros[0]+patchmacros[1]+lines_added+1, "# << gbp-apply-patches    # auto-added by gbp\n")
            lines_added += 2
        else:
            gbp.log.warn("Couldn't determine position where to add gbp-apply-patches autoupdate markers")

        # write new spec
        tmpffd, tmpfpath = tempfile.mkstemp(suffix='.spec', dir='.')
        tmpf = os.fdopen(tmpffd, 'w')
        tmpf.writelines(lines)

        shutil.move(tmpfpath, self.specfile)

        return (len(lines)-numlines)


    def updatepatches(self, patchfilenames):
        """Update spec file with a new set of patches"""
        autoupdate_tags = set(["gbp-patch-tags", "gbp-apply-patches"])
        autoupdate_found_tags = set()

        f = file(self.specfile)
        tmpffd, tmpfpath = tempfile.mkstemp(suffix='.spec', dir='.')
        tmpf = os.fdopen(tmpffd, 'w')

        # Check the max patchnumber of non-autoupdate patches
        start_patch_tag_num = 0
        for n, p in self.patches.iteritems():
            if (not p['autoupdate']) and (n >= start_patch_tag_num):
                start_patch_tag_num = n + 1
        gbp.log.debug("Starting autoupdate patch macro numbering from %s" % start_patch_tag_num)

        autoupdate = False
        for line in f:
            m = self.marker_re.match(line)

            # Write to tmpfile as is, if not in autoupdate section
            if m or not autoupdate:
                tmpf.write(line)

            if m:
                if m.group('what') in autoupdate_tags:
                    if m.group('marker') == '>>':
                        if autoupdate:
                            raise GbpError, "New autoupdate start marker found before previous ends. Please fix the .spec file."
                        autoupdate = m.group('what')
                        autoupdate_found_tags.add(autoupdate)

                        if autoupdate == 'gbp-patch-tags':
                            for i in range(len(patchfilenames)):
                                tag_num = start_patch_tag_num + i
                                # "PatchXYZ:" text 12 chars wide, left aligned
                                tmpf.write("%-12s%s\n" % ("Patch%d:" % tag_num, patchfilenames[i]))
                        elif autoupdate == 'gbp-apply-patches':
                            for i in range(len(patchfilenames)):
                                tag_num = start_patch_tag_num + i
                                tmpf.write("# %s\n" % patchfilenames[i])
                                tmpf.write("%%patch%d -p1\n" % tag_num)
                        else:
                            # Unknown autoupdate marker, we shouldn't end up here
                            gbp.log.warn("Hmm, found a bug - don't know what to do with marker '%s'" % autoupdate)
                    else:
                        if not autoupdate:
                            raise GbpError, "An orphan autoupdate stop marker found (no matching start marker). Please fix the .spec file."
                        if autoupdate != m.group('what'):
                            raise GbpError, "Stop marker name does not match the start marker. Please fix the .spec file."
                        autoupdate = None
                else:
                    gbp.log.debug("Unknown autoupdate marker '%s', skipping..." % m.group('what'))

        tmpf.close()
        f.close()

        if autoupdate:
            raise GbpError, "No stop marker found for '%s'. Please fix the .spec file." % autoupdate
        if len(autoupdate_found_tags) != len(autoupdate_tags):
            gbp.log.warn("Not all autoupdate sections found, spec file might be incompletely update. Please check it manually.")

        shutil.move(tmpfpath, self.specfile)

    def patchseries(self):
        """
        Return patches of the RPM as a gbp patchseries
        """
        series = PatchSeries()
        patchdir = os.path.dirname(self.specfile)
        for n, p in sorted(self.patches.iteritems()):
            series.append(Patch(os.path.join(patchdir, p['filename']), strip = int(p['strip'])))
        return series


    def guess_orig_file(self):
        """
        Try to guess the name of the primary upstream/source tarball
        returns a tuple with tarball filename and compression suffix
        """
        tarball_re = re.compile(
            r'(?P<base>(?P<name>%s)?.*)?\.(?P<format>tar|zip|tgz)\.?(?P<comp>(bz2|gz|xz|lzma|\b))$' % \
               self.specinfo.packages[0].header[rpm.RPMTAG_NAME])
        tarball = ""
        base = ""
        comp = ""
        formt = ""

        # Take the first file that starts 'name' and has suffix like 'tar.*'
        for (name, num, typ) in sorted(self.specinfo.sources, key=lambda s: s[1]):
            # only check files of source type
            if typ == 1:
                m = tarball_re.match(os.path.basename(name))
                if m:
                    # Take the first tarball that starts with pkg name
                    if m.group('name'):
                        tarball = name
                        base = m.group('base')
                        comp = m.group('comp')
                        formt = m.group('format')
                        break
                    # otherwise we only take the first tarball
                    elif not tarball:
                        tarball = name
                        base = m.group('base')
                        comp = m.group('comp')
                        formt = m.group('format')
                    # else don't accept
        if (formt, comp) == ('tgz', ''):
            formt, comp = 'tar', 'gz'
        return (tarball, base, formt, comp)


    def debugprint(self):
        """
        Print info about the spec in readable way
        """
        gbp.log.debug("Name: %s" % (self.name))
        gbp.log.debug("Version: %s" % (self.version))
        gbp.log.debug("Release: %s" % self.release)
        gbp.log.debug("Epoch: %s" % self.epoch)
        gbp.log.debug("Spec file: %s" % self.specfile)
        gbp.log.debug("Orig file: %s" % self.orig_file)

        for n, p in sorted(self.patches.iteritems()):
            gbp.log.debug("Patch %s: %s, strip: %s, apply: %s, autoupdate: %s" %
                          (n, p['filename'], p['strip'], p['apply'], p['autoupdate']))


def parse_srpm(srpmfile):
    """parse srpm by creating a SrcRpmFile object"""
    try:
        srcrpm = SrcRpmFile(srpmfile)
    except IOError, err:
        raise GbpError, "Error reading src.rpm file: %s" % err
    except rpm.error, err:
        raise GbpError, "RPM error while reading src.rpm: %s" % err

    return srcrpm


def parse_spec(specfile):
    try:
        return SpecFile(specfile)
    except IOError, err:
        raise GbpError, "Error reading spec file: %s" % err


def find_files(topdir, filespec='*', recursive=True):
    """find spec files in given dir"""
    cmd = 'find -L %s' % topdir
    if not recursive:
        cmd += " -maxdepth 1"
    cmd += ' -name "%s" -type f' % filespec

    files = []
    for f in os.popen(cmd):
        # Strip the newline from the end
        files.append(f[:-1])

    return files

def guess_spec(topdir):
    """Guess a spec file"""
    specs = find_files(topdir, '*.spec', recursive=False)
    if len(specs) == 0:
        specs = find_files(topdir, '*.spec', recursive=True)

    if len(specs) == 0:
        raise NoSpecError, ("No spec file found.")
    elif len(specs) > 1:
        raise NoSpecError, ("Multiple spec files found, don't know which to use.")

    # strip './' from the beginning
    spec = re.match(r'(?:./)*([^/].*)', specs[0]).group(1)
    return (os.path.dirname(spec), spec)

def guess_spec_repo(repo, branch, packaging_dir):
    """
    @todo: implement this
    Try to find/parse the spec file from given branch in the git
    repository.
    """
    raise NoSpecError, "Searching spec from other branch not implemented yet"


# vim:et:ts=4:sw=4:et:sts=4:ai:set list listchars=tab\:»·,trail\:·:
