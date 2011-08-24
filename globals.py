import sys
import re

ICON_MIMETYPE_BLACKLIST = [
  'application/xml',
  'text/html',
  'text/plain',
]

MIN_ICON_LENGTH = 100
MAX_ICON_LENGTH = 20000

MC_CACHE_TIME = 2419200 # seconds (28 days)

KEY_FORMAT = 'icon_loc-%s'

RE_URLDECODE = re.compile('%([0-9a-hA-H][0-9a-hA-H])', flags=re.MULTILINE)
RE_LINKTAG = re.compile('^(shortcut|icon|shortcut icon)$', flags=re.IGNORECASE)
RE_METAREFRESH = re.compile('url=([^;]+)', flags=re.IGNORECASE)

DEFAULT_FAVICON_LOC = 'http://d3gibmfbqm9w63.cloudfront.net/img/static/default_favicon.png'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows; U; Windows NT 6.1; en-US; ' +
                                  'rv:1.9.2.13) Gecko/20101203 Firefox/3.6.13'}

FILECOMMAND_BSD = ['file','-','-I']
FILECOMMAND_SYSV = ['file','-','-i']

if sys.platform.startswith('linux'):
  FILECOMMAND = FILECOMMAND_SYSV
elif sys.platform.startswith('darwin'):
  FILECOMMAND = FILECOMMAND_BSD
else:
  print "missing platform: %s, defaulting to SYSV" % sys.platform
  FILECOMMAND = FILECOMMAND_SYSV


CONNECTION_TIMEOUT = 10
TIMEOUT = 15

# vim: sts=2:sw=2:ts=2:tw=85:cc=85
