# vim: set fileencoding=utf-8 :
#
# (C) 2010 Guido Guenther <agx@sigxcpu.org>
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
"""Simple colored logging classes"""

import os
import sys
import logging

COLORS = dict([('none', 0)] + zip(['black', 'red', 'green', 'yellow', 'blue',
                                   'magenta', 'cyan', 'white'], range(30, 38)))

DEFAULT_COLOR_SCHEME = {logging.DEBUG: COLORS['green'],
                        logging.INFO: COLORS['green'],
                        logging.WARNING: COLORS['red'],
                        logging.ERROR: COLORS['red'],
                        logging.CRITICAL: COLORS['red']}


class GbpStreamHandler(logging.StreamHandler):
    """Special stream handler for enabling colored output"""

    COLOR_SEQ = "\033[%dm"
    OFF_SEQ = "\033[0m"

    def __init__(self, stream=None, color=True):
        super(GbpStreamHandler, self).__init__(stream)
        self._color = color
        self._color_scheme = DEFAULT_COLOR_SCHEME
        msg_fmt = "%(name)s:%(levelname)s: %(message)s"
        self.setFormatter(logging.Formatter(fmt=msg_fmt))

    def set_color(self, color):
        """Set/unset colorized output"""
        self._color = color

    def set_color_scheme(self, color_scheme={}):
        """Set logging colors"""
        self._color_scheme = DEFAULT_COLOR_SCHEME
        self._color_scheme.update(color_scheme)

    def format(self, record):
        """Colorizing formatter"""
        msg = super(GbpStreamHandler, self).format(record)
        # Never write color-escaped output to non-tty streams
        if self._color and self.stream.isatty():
            return (self.COLOR_SEQ % self._color_scheme[record.levelno] +
                    msg + self.OFF_SEQ)
        else:
            return msg


class GbpLogger(logging.Logger):
    """Logger class for git-buildpackage"""

    def __init__(self, name, color=True, *args, **kwargs):
        super(GbpLogger, self).__init__(name, *args, **kwargs)
        self._default_handler = GbpStreamHandler(sys.stdout, color)
        self.addHandler(self._default_handler)

    def set_color(self, color):
        """Set/unset colorized output of the default handler"""
        self._default_handler.set_color(color)

    def set_color_scheme(self, color_scheme={}):
        """Set the color scheme of the default handler"""
        self._default_handler.set_color_scheme(color_scheme)


def err(msg):
    """Logs a message with level ERROR on the GBP logger"""
    LOGGER.error(msg)

def warn(msg):
    """Logs a message with level WARNING on the GBP logger"""
    LOGGER.warning(msg)

def info(msg):
    """Logs a message with level INFO on the GBP logger"""
    LOGGER.info(msg)

def debug(msg):
    """Logs a message with level DEBUG on the GBP logger"""
    LOGGER.debug(msg)

def _use_color(color):
    """Parse the color option"""
    if isinstance(color, bool):
        return color
    else:
        if color.is_on():
            return True
        elif color.is_auto():
            in_emacs = (os.getenv("EMACS") and
                        os.getenv("INSIDE_EMACS", "").endswith(",comint"))
            return not in_emacs
    return False

def _parse_color_scheme(color_scheme=""):
    """Set logging colors"""
    scheme = {}
    colors = color_scheme.split(':')
    levels = (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR)
    for field, color in enumerate(colors):
        level = levels[field]
        try:
            scheme[level] = int(color)
        except ValueError:
            try:
                scheme[level] = COLORS[color.lower()]
            except KeyError: pass
    return scheme

def setup(color, verbose, color_scheme=""):
    """Basic logger setup"""
    LOGGER.set_color(_use_color(color))
    LOGGER.set_color_scheme(_parse_color_scheme(color_scheme))
    if verbose:
        LOGGER.setLevel(logging.DEBUG)
    else:
        LOGGER.setLevel(logging.INFO)


# Initialize the module
logging.setLoggerClass(GbpLogger)

LOGGER = logging.getLogger("gbp")

