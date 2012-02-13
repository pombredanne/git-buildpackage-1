#!/usr/bin/make

MANS = git-buildpackage  \
           git-import-dsc    \
           git-import-dscs   \
           git-import-orig   \
           git-dch           \
           gbp-pull          \
           gbp-clone         \
           gbp-pq            \
           gbp-create-remote-repo \
           gbp.conf

MANUAL=manual-html
SGML_MANPAGES=$(patsubst %,%.1,$(MANS))
POD_MANPAGES=git-pbuilder.1
MANPAGES=$(SGML_MANPAGES) $(POD_MANPAGES)
VERSION_ENT=version.ent
GBP_VERSION=../gbp/version.py
DEB_VERSION=$(shell sed -ne 's/^gbp_version="\([.a-z0-9~]\+\)"/\1/p' $(GBP_VERSION))
CHANGELOG=../debian/changelog

# Stuff to build docs outside Debian
HAVE_SGML2X ?= 1
GTK_DOC_CATALOG_FILE ?= /usr/share/sgml/gtk-doc/gtk-doc.cat

all: $(MANUAL) $(MANPAGES)

$(MANUAL): manual.sgml chapters/*.sgml manpages/*.sgml
ifeq ($(HAVE_SGML2X),1)
	docbook-2-html -s local $<
else
	docbook2html -d local-nosgml2x.dsl -c $(GTK_DOC_CATALOG_FILE) -o $(MANUAL) $<
endif
	cp /usr/share/gtk-doc/data/*.png $(MANUAL)

%.1: man.%.sgml manpages/%.sgml
	docbook2man -o . $<

git-pbuilder.1: ../bin/git-pbuilder
	pod2man $< $@

manual.sgml: $(VERSION_ENT)

$(VERSION_ENT): $(GBP_VERSION)
	echo '<!ENTITY gbp-version "$(DEB_VERSION)">' > $(VERSION_ENT)

clean:
	-rm -r manual-html/
	-rm *.1 *.5 manpage.* $(VERSION_ENT)

