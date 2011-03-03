import cherrypy
import os.path
import json

from re import compile, MULTILINE, IGNORECASE
from urlparse import urlparse, urljoin
from urllib import FancyURLopener
from datetime import datetime, timedelta
from BeautifulSoup import BeautifulSoup
from jinja2 import Environment, FileSystemLoader
from memcache import Client
from logging import handlers, DEBUG, INFO, WARNING

from globals import *

class FakeUserAgentOpener(FancyURLopener):
  version = 'Mozilla/5.0 (Windows; U; Windows NT 6.1; en-US; rv:1.9.2.13) Gecko/20101203 Firefox/3.6.13'

class BaseHandler(object):

  def __init__(self):
    super(BaseHandler, self).__init__()
    self.re = compile('%([0-9a-hA-H][0-9a-hA-H])', MULTILINE)
  
  def htc(self, m):
    return chr(int(m.group(1), 16))
  
  def urldecode(self, url):
    return self.re.sub(self.htc, url)


class PrintFavicon(BaseHandler):

  def __init__(self):
    super(PrintFavicon, self).__init__()

    self.urlopener = FakeUserAgentOpener()
    
    self.default_icon = self.urlopener.open(DEFAULT_FAVICON_LOC).read()
    
    self.env = Environment(loader=FileSystemLoader(os.path.join(cherrypy.config['favicon.root'], 'templates')))
    
    self.mc = Client(['%(memcache.host)s:%(memcache.port)d' % cherrypy.config], debug=0)

    # Initialize counters
    for counter in ['requests', 'hits', 'defaults']:
      self.mc.add('counter-%s' % counter, '0')

  def validateIconResponse(self, iconResponse):
    if iconResponse.getcode() != 200:
      cherrypy.log('Non-success response(%s) fetching %s' % (iconResponse.getcode(), iconResponse.geturl()),
                   severity=INFO)
      return None

    iconContentType = iconResponse.info().gettype()
    if iconContentType in ICON_MIMETYPE_BLACKLIST:
      cherrypy.log('Url:%s favicon content-Type:%s is blacklisted' % (iconResponse.geturl(), iconContentType),
                   severity=INFO)
      return None

    icon = iconResponse.read()
    iconLength = len(icon)

    if iconLength < MIN_ICON_LENGTH or iconLength > MAX_ICON_LENGTH:
      cherrypy.log('Url:%s content length:%d out of bounds' % (iconResponse.geturl(), iconLength),
                   severity=INFO)
      return None
  
    return icon

  # Icon at [domain]/favicon.ico?
  def iconAtRoot(self, targetDomain):
    rootIconPath = targetDomain + '/favicon.ico'
    try:
      rootDomainFaviconResult = self.urlopener.open(rootIconPath)
      rootIcon = self.validateIconResponse(rootDomainFaviconResult)
      if rootIcon:
        cherrypy.log('Found favicon for %s at domain root' % targetDomain, severity=DEBUG)
        self.cacheIcon(targetDomain, rootIcon, rootIconPath)
        return rootIcon
    except:
      cherrypy.log('Error fetching favicon at root for domain : %s' % targetDomain, severity=WARNING, traceback=True)

  # Icon specified in page?
  def iconInPage(self, targetDomain, targetPath):
    cherrypy.log('Attempting to locate embedded favicon link in page for %s' % targetPath, severity=DEBUG)
    try:
      rootDomainPageResult = self.urlopener.open(targetPath)
      if rootDomainPageResult.getcode() == 200:
        pageSoup = BeautifulSoup(rootDomainPageResult.read())
        pageSoupIcon = pageSoup.find('link', rel=compile('^(shortcut|icon|shortcut icon)$', IGNORECASE))
        if pageSoupIcon:
          pageIconHref = pageSoupIcon.get('href')
          if pageIconHref:
            pageIconPath = urljoin(targetPath, pageIconHref if not pageIconHref[0] == '/' else pageIconHref[1:])
            pagePathFaviconResult = self.urlopener.open(pageIconPath)
            pageIcon = self.validateIconResponse(pagePathFaviconResult)
            if pageIcon:
              cherrypy.log('Found favicon for %s at %s' % (targetDomain, pageIconPath), severity=DEBUG)
              self.cacheIcon(targetDomain, pageIcon, pageIconPath)
              return pageIcon
        else:
          cherrypy.log('No link tag found in %s' % targetPath, severity=DEBUG)
      else:
        cherrypy.log('Non-success response(%d) for %s' % (rootDomainPageResult.getcode(), targetPath), 
                     severity=INFO)
    except:
      cherrypy.log('Error extracting favicon from page for: %s' % targetPath, severity=WARNING, traceback=True)

  def cacheIcon(self, domain, icon, loc):
    self.mc.set_multi({'icon-%s' % domain : icon, 'icon_loc-%s' % domain : str(loc)}, time=MC_CACHE_TIME)

  def iconInCache(self, targetDomain):
    icon = self.mc.get('icon-%s' % targetDomain)
    if icon:
      cherrypy.log('Cache hit : %s' % targetDomain, severity=DEBUG)
      self.mc.incr('counter-hits')
      cherrypy.response.headers['X-Cache'] = 'Hit'
      if icon == 'DEFAULT':
        self.mc.incr('counter-defaults')
        return self.default_icon
      else:
        return icon

  def writeIcon(self, icon):
    self.writeHeaders()
    return icon

  def writeHeaders(self, fmt='%a, %d %b %Y %H:%M:%S %z'):
    # MIME Type
    cherrypy.response.headers['Content-Type'] = 'image/x-icon'

    # Set caching headers
    cherrypy.response.headers['Cache-Control'] = 'public, max-age=2592000'
    cherrypy.response.headers['Expires'] = (datetime.now() + timedelta(days=30)).strftime(fmt)

  def parse(self, url):
    # Get page path
    targetPath = self.urldecode(url)
    if not targetPath.startswith('http'):
      targetPath = 'http://%s' % targetPath
    cherrypy.log('Decoded URL : %s' % targetPath, severity=DEBUG)

    # Split path to get domain
    targetURL = urlparse(targetPath)
    if not targetURL or not targetURL.scheme or not targetURL.netloc:
      raise cherrypy.HTTPError(400, 'Malformed URL:%s' % url)

    targetDomain = '%s://%s' % (targetURL.scheme, targetURL.netloc)
    cherrypy.log('URL : %s, domain : %s' % (targetPath, targetDomain), severity=DEBUG)

    return (targetPath, targetDomain)

  @cherrypy.expose
  def index(self):
    status = { 'status' : 'ok', 'counters' : {} }
    for counter in ['requests', 'hits', 'defaults']:
      status['counters'][counter] = self.mc.get('counter-%s' %counter)
    return json.dumps(status)

  @cherrypy.expose
  def test(self):
    topSites = open(os.path.join(cherrypy.config['favicon.root'], 'topsites.txt'), 'r').read().split()
    template = self.env.get_template('test.html')
    return template.render(topSites=topSites)

  @cherrypy.expose
  def clear(self, url):
    cherrypy.log('Incoming cache invalidation request : %s' % url, severity=DEBUG)

    targetPath, targetDomain = self.parse(url)
    self.mc.delete_multi(['icon-%s' % targetDomain, 'icon_loc-%s' % targetDomain])

    cherrypy.log('Evicted cache entry for %s' % targetDomain, severity=INFO)

  @cherrypy.expose
  def favicon(self, url, skipCache=False):
    cherrypy.log('Incoming request : %s (skipCache=%s)' % (url, skipCache), severity=DEBUG)

    self.mc.incr('counter-requests')

    targetPath, targetDomain = self.parse(url)

    icon = (not skipCache and self.iconInCache(targetDomain)) or \
           self.iconInPage(targetDomain, targetPath) or \
           self.iconAtRoot(targetDomain)

    if not icon:
      icon = self.default_icon
      cherrypy.log('Falling back to default icon for : %s' % targetDomain, severity=WARNING)
      self.cacheIcon(targetDomain, 'DEFAULT', DEFAULT_FAVICON_LOC)
      self.mc.incr('counter-defaults')

    return self.writeIcon(icon)


if __name__ == '__main__':
  # Remove the default FileHandlers if present.
  cherrypy.log.error_file = ''
  cherrypy.log.access_file = ''

  # Make a new RotatingFileHandler for the error log.
  fname = getattr(cherrypy.log, 'rot_error_file', 'error.log')
  handler = handlers.TimedRotatingFileHandler(fname, 'midnight', 1, 7)
  handler.setLevel(DEBUG)
  handler.setFormatter(cherrypy._cplogging.logfmt)
  cherrypy.log.error_log.addHandler(handler)

  # Make a new RotatingFileHandler for the access log.
  fname = getattr(cherrypy.log, 'rot_access_file', 'access.log')
  handler = handlers.TimedRotatingFileHandler(fname, 'midnight', 1, 7)
  handler.setLevel(DEBUG)
  handler.setFormatter(cherrypy._cplogging.logfmt)
  cherrypy.log.access_log.addHandler(handler)

  conf = os.path.join(os.path.dirname(__file__), 'dev.conf')
  cherrypy.quickstart(PrintFavicon(), config=conf)

