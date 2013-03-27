#!/bin/bash -e
_PKG_NAME=git-buildpackage
_SPECFILE=${_PKG_NAME}.spec
_PKG_VERSION=`grep '^Version: ' $_SPECFILE | awk '{print $2}'`
_TARBALL=${_PKG_NAME}_${_PKG_VERSION}.tar.gz

echo "Updating dsc file to version $_PKG_VERSION"
_TARBALL_BYTES=` stat -c '%s' $_TARBALL`
_MD5=`md5sum $_TARBALL | sed "s/  / $_TARBALL_BYTES /"`
sed  -i "s/^Version:.*/Version: ${_PKG_VERSION}/" ${_PKG_NAME}.dsc
sed  -i "s/ [a-f0-9]\+ [0-9]\+ ${_PKG_NAME}.*tar.*/ ${_MD5}/" ${_PKG_NAME}.dsc

