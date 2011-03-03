import cherrypy
import os.path
import json

from re import compile, MULTILINE, IGNORECASE
from urlparse import urlparse, urljoin
from urllib2 import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener
from datetime import datetime, timedelta
from BeautifulSoup import BeautifulSoup
from jinja2 import Environment, FileSystemLoader
from memcache import Client
from logging import handlers, DEBUG, INFO, WARNING, ERROR

from globals import *

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

    self.default_icon = self.open(DEFAULT_FAVICON_LOC).read()
    
    self.env = Environment(loader=FileSystemLoader(os.path.join(cherrypy.config['favicon.root'], 'templates')))
    
    self.mc = Client(['%(memcache.host)s:%(memcache.port)d' % cherrypy.config], debug=0)

    # Initialize counters
    for counter in ['requests', 'hits', 'defaults']:
      self.mc.add('counter-%s' % counter, '0')

  def open(self, url, headers=None):
    if not headers:
      headers = dict()
    headers.update({'User-Agent': 'Mozilla/5.0 (Windows; U; Windows NT 6.1; en-US; rv:1.9.2.13) Gecko/20101203 Firefox/3.6.13'})
    opener = build_opener(HTTPRedirectHandler(), HTTPCookieProcessor())
    return opener.open(Request(url, headers=headers))

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

    if iconLength == 0:
      cherrypy.log('Url:%s null content length' % iconResponse.geturl(), severity=INFO)
      return None

    if iconLength < MIN_ICON_LENGTH or iconLength > MAX_ICON_LENGTH:
      # Issue warning, but accept nonetheless!
      cherrypy.log('Identified url:%s content length:%d out of bounds' % (iconResponse.geturl(), iconLength),
                   severity=WARNING)
  
    return icon

  # Icon at [domain]/favicon.ico?
  def iconAtRoot(self, targetDomain):
    cherrypy.log('Attempting to locate favicon for %s at domain root' % targetDomain, severity=DEBUG)
    rootIconPath = targetDomain + '/favicon.ico'
    try:
      rootDomainFaviconResult = self.open(rootIconPath)
      rootIcon = self.validateIconResponse(rootDomainFaviconResult)
      if rootIcon:
        cherrypy.log('Found favicon for %s at domain root' % targetDomain, severity=DEBUG)
        self.cacheIconLoc(targetDomain, rootIconPath)
        return (rootIcon, rootIconPath)
    except:
      cherrypy.log('Error fetching favicon at root for domain : %s\n' % targetDomain, severity=WARNING, traceback=True)

  # Icon specified in page?
  def iconInPage(self, targetDomain, targetPath):
    cherrypy.log('Attempting to locate embedded favicon link in page for %s' % targetDomain, severity=DEBUG)

    try:
      rootDomainPageResult = self.open(targetPath)

      if rootDomainPageResult.getcode() == 200:
        pageSoup = BeautifulSoup(rootDomainPageResult.read())
        pageSoupIcon = pageSoup.find('link', rel=compile('^(shortcut|icon|shortcut icon)$', IGNORECASE))

        if pageSoupIcon:
          pageIconHref = pageSoupIcon.get('href')

          if pageIconHref:
            if pageIconHref.startswith('//'):
              pageIconPath = '%s:%s' % (urlparse(rootDomainPageResult.geturl()).scheme, pageIconHref)
            else:
              pageIconPath = urljoin(targetPath, pageIconHref if not pageIconHref[0] == '/' else pageIconHref[1:])
            cherrypy.log('Found embedded favicon link : %s for : %s' % (pageIconPath, targetDomain), severity=DEBUG)

            cookies = rootDomainPageResult.headers.getheaders("Set-Cookie")
            headers = None
            if cookies:
              headers = {'Cookie': ';'.join(cookies)}

            pagePathFaviconResult = self.open(pageIconPath, headers=headers)

            pageIcon = self.validateIconResponse(pagePathFaviconResult)
            if pageIcon:
              cherrypy.log('Found favicon for : %s at : %s' % (targetDomain, pageIconPath), severity=DEBUG)
              self.cacheIconLoc(targetDomain, pageIconPath)
              return (pageIcon, pageIconPath)
        else:
          cherrypy.log('No link tag found in %s' % targetPath, severity=DEBUG)
      else:
        cherrypy.log('Non-success response(%d) for %s' % (rootDomainPageResult.getcode(), targetPath), 
                     severity=INFO)
    except:
      cherrypy.log('Error extracting favicon from page for : %s\n' % targetPath, severity=WARNING, traceback=True)

  def cacheIconLoc(self, domain, loc):
    if not self.mc.set('icon_loc-%s' % domain, str(loc), time=MC_CACHE_TIME):
      cherrypy.log('Could not cache icon location for : %s' % domain, severity=ERROR)

  def iconInCache(self, targetDomain):
    icon_loc = self.mc.get('icon_loc-%s' % targetDomain)
    if icon_loc:
      cherrypy.log('Cache hit : %s, location : %s' % (targetDomain, icon_loc), severity=DEBUG)
      if icon_loc == DEFAULT_FAVICON_LOC:
        self.mc.incr('counter-hits')
        self.mc.incr('counter-defaults')
        cherrypy.response.headers['X-Cache'] = 'Hit'
        return (self.default_icon, DEFAULT_FAVICON_LOC)
      else:
        iconResult = self.open(icon_loc)
        icon = self.validateIconResponse(iconResult)
        if icon:
          self.mc.incr('counter-hits')
          cherrypy.response.headers['X-Cache'] = 'Hit'
          return (icon, icon_loc)
        else:
          cherrypy.log('Cached location for %s no longer valid' % targetDomain, severity=WARNING)

  def writeIcon(self, icon, icon_loc):
    self.writeHeaders(icon_loc)
    return icon

  def writeHeaders(self, icon_loc, fmt='%a, %d %b %Y %H:%M:%S %z'):
    # MIME Type
    mime = None
    try:
      ext = icon_loc[icon_loc.rindex('.'):]
      if ext == '.png':
        mime = 'image/png'
      elif ext == '.gif':
        mime = 'image/gif'
      elif ext == '.ico':
        mime = 'image/x-icon'
    except ValueError:
      pass

    if not mime:
      cherrypy.log('MIME type could not be determined for : %s' % icon_loc, severity=WARNING)
      mime = 'image/x-icon'

    cherrypy.response.headers['Content-Type'] = mime

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
    status = {'status': 'ok', 'counters': dict()}
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
    self.mc.delete('icon_loc-%s' % targetDomain)

    cherrypy.log('Evicted cache entry for %s' % targetDomain, severity=INFO)

  @cherrypy.expose
  def s(self, url, skipCache=False):
    cherrypy.log('Incoming request : %s (skipCache=%s)' % (url, skipCache), severity=DEBUG)

    self.mc.incr('counter-requests')

    targetPath, targetDomain = self.parse(url)

    result = (not skipCache and self.iconInCache(targetDomain)) or \
             self.iconInPage(targetDomain, targetPath) or \
             self.iconAtRoot(targetDomain)

    if not result:
      cherrypy.log('Falling back to default icon for : %s' % targetDomain, severity=WARNING)
      self.cacheIconLoc(targetDomain, DEFAULT_FAVICON_LOC)
      self.mc.incr('counter-defaults')
      icon, icon_loc = self.default_icon, DEFAULT_FAVICON_LOC
    else:
      icon, icon_loc = result

    return self.writeIcon(icon, icon_loc)


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

