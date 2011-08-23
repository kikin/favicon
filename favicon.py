import StringIO
import cherrypy
import gzip
import json
import memcache
import os, os.path
import re
#import signal
import subprocess
import sys
import urllib2
import urlparse

import globals

from BeautifulSoup import BeautifulSoup
from datetime import datetime, timedelta
from jinja2 import Environment, FileSystemLoader
from logging import DEBUG, INFO, WARN, ERROR, Formatter
from time import time

# helper methods

def timeout_handler(signum, frame):
  raise TimeoutError()

def libmagic(string):
  '''example out= '/dev/stdin: image/x-ico; charset=binary'
  out.split()[1][0:-1] = image/x-ico'''
  process = subprocess.Popen(globals.FILECOMMAND_BSD,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE)
  out, err = process.communicate(input=string)
  return out.split()[1][0:-1]

def gunzip(stream):
  '''don't use for even moderately big files'''
  f = StringIO.StringIO(stream)
  output = gzip.GzipFile(fileobj=f).read()
  f.close()
  return output

# classes

class Icon(object):

  def __init__(self, data=None, location=None, type=None):
    super(Icon, self).__init__()
    self.data = data
    self.location = location
    self.type = type

class TimeoutError(Exception):

  def __str__(self):
    return repr(TimeoutError)

class BaseHandler(object):

  def __init__(self):
    super(BaseHandler, self).__init__()
    self.re = re.compile('%([0-9a-hA-H][0-9a-hA-H])', re.MULTILINE)

  def htc(self, m):
    return chr(int(m.group(1), 16))

  def urldecode(self, url):
    return self.re.sub(self.htc, url)


class PrintFavicon(BaseHandler):

  def __init__(self):
    super(PrintFavicon, self).__init__()

    default_icon_data = self.open(globals.DEFAULT_FAVICON_LOC, time()).read()
    self.default_icon = Icon(data=default_icon_data,
        location=globals.DEFAULT_FAVICON_LOC,
        type='image/png')

    self.env = Environment(loader=FileSystemLoader(
      os.path.join(cherrypy.config['favicon.root'], 'templates')))

    self.mc = memcache.Client(['%(memcache.host)s:%(memcache.port)d' %
                cherrypy.config], debug=2)

    # Initialize counters
    for counter in ['requests', 'hits', 'defaults']:
      self.mc.add('counter-%s' % counter, '0')

  def open(self, url, start, headers=None):
    time_spent = int(time() - start)
    if time_spent >= globals.TIMEOUT:
      raise TimeoutError(time_spent)

    if not headers:
      headers = dict()
    headers.update(globals.HEADERS)

    opener = urllib2.build_opener(urllib2.HTTPCookieProcessor())
    result = opener.open(urllib2.Request(url, headers=headers),
                       timeout=min(globals.CONNECTION_TIMEOUT, globals.TIMEOUT - time_spent))
    cherrypy.log('%s =redirect=> %s' % (url, result.url), severity=DEBUG)

    return result

  def validateIconResponse(self, iconResponse):
    #these methods need to be cleaned up
    if iconResponse.getcode() != 200:
      cherrypy.log('Non-success response:%d fetching url:%s' % \
                   (iconResponse.getcode(), iconResponse.geturl()),
                   severity=DEBUG)
      return None

    icon = iconResponse.read()
    iconLength = len(icon)

    if iconLength == 0:
      cherrypy.log('Url:%s null content length' % iconResponse.geturl(),
                   severity=DEBUG)
      return None

    iconContentType = iconResponse.info().gettype()
    #hopefully the icon sent is never super duper big
    try:
      iconContentTypeMagic = libmagic(icon)
      if 'gzip' in iconContentTypeMagic.lower():
        cherrypy.log('Type of %s is gzip, unzipping...' % iconResponse.geturl(),
                    severity=WARN)
        icon = gunzip(icon)
        #checking mimetype again
        iconContentTypeMagic = libmagic(icon)

    except Exception as e:
      iconContentTypeMagic = iconContentType
      cherrypy.log('Error calling libmagic and gzip on %s: %s' % (iconResponse.geturl(), e),
                    severity=ERROR)

    if iconContentTypeMagic in "image/x-ico":
      iconContentTypeMagic = "image/x-icon"

    if (iconContentType != iconContentTypeMagic):
      cherrypy.log('Url:%s Content-Type does not match type from libmagic' % \
                   iconResponse.geturl(), severity=WARN)
      cherrypy.log('Content-Type sent: %s, scanned Content-Type: %s' % \
                   (iconContentType, iconContentTypeMagic),
                   severity=WARN)

    if iconContentTypeMagic in globals.ICON_MIMETYPE_BLACKLIST:
      cherrypy.log('Url:%s favicon content-Type:%s blacklisted' % \
                   (iconResponse.geturl(), iconContentType),
                   severity=WARN)
      return None

    if iconLength < globals.MIN_ICON_LENGTH or iconLength > globals.MAX_ICON_LENGTH:
      # Issue warning, but accept nonetheless!
      cherrypy.log('Warning: url:%s favicon size:%d out of bounds' % \
                   (iconResponse.geturl(), iconLength),
                   severity=WARN)

    return Icon(data=icon, type=iconContentTypeMagic)

  # Icon at [domain]/favicon.ico?
  def iconAtRoot(self, targetDomain, start):
    cherrypy.log('Attempting to locate favicon for domain:%s at root' % \
                 targetDomain,
                 severity=WARN)

    rootIconPath = urlparse.urljoin(targetDomain, '/favicon.ico')

    try:
      rootDomainFaviconResult = self.open(rootIconPath, start)
      rootIcon = self.validateIconResponse(rootDomainFaviconResult)

      if rootIcon:
        cherrypy.log('Found favicon for domain:%s at root' % targetDomain,
                     severity=DEBUG)

        self.cacheIconLoc(targetDomain, rootIconPath)
        rootIcon.location = rootIconPath
        return rootIcon

    except:
      cherrypy.log('Error fetching favicon at domain root:%s, err:%s, msg:%s' % \
                   (targetDomain, sys.exc_info()[0], sys.exc_info()[1]),
                   severity=DEBUG)

  # Icon specified in page?
  def iconInPage(self, targetDomain, targetPath, start, refresh=True):
    cherrypy.log('Attempting to locate embedded favicon link in page:%s' % \
                 targetPath,
                 severity=DEBUG)

    try:
      rootDomainPageResult = self.open(targetPath, start)

      if rootDomainPageResult.getcode() == 200:
        pageSoup = BeautifulSoup(rootDomainPageResult.read())
        pageSoupIcon = pageSoup.find('link',
                                     rel=re.compile('^(shortcut|icon|shortcut icon)$',
                                     re.IGNORECASE))

        if pageSoupIcon:
          pageIconHref = pageSoupIcon.get('href')

          if pageIconHref:
            pageIconPath = urlparse.urljoin(targetPath, pageIconHref)
            cherrypy.log('Found embedded favicon link:%s for domain:%s' % \
                         (pageIconPath, targetDomain),
                         severity=DEBUG)

            cookies = rootDomainPageResult.headers.getheaders("Set-Cookie")
            headers = None
            if cookies:
              headers = {'Cookie': ';'.join(cookies)}

            pagePathFaviconResult = self.open(pageIconPath,
                                              start,
                                              headers=headers)

            pageIcon = self.validateIconResponse(pagePathFaviconResult)
            if pageIcon:
              cherrypy.log('Found favicon at:%s for domain:%s' % \
                           (pageIconPath, targetDomain),
                           severity=DEBUG)

              self.cacheIconLoc(targetDomain, pageIconPath)
              pageIcon.location = pageIconPath
              return pageIcon

        else:
          if refresh:
            for meta in pageSoup.findAll('meta'):
              if meta.get('http-equiv', '').lower() == 'refresh':
                match = re.search('url=([^;]+)',
                               meta.get('content', ''),
                               flags=re.IGNORECASE)

                if match:
                  refreshPath = urlparse.urljoin(rootDomainPageResult.geturl(),
                                        match.group(1)).strip()

                  cherrypy.log('Processing refresh directive:%s for domain:%s' % \
                               (refreshPath, targetDomain),
                               severity=DEBUG)

                  icon = self.iconInPage(targetDomain,
                                         refreshPath,
                                         start,
                                         refresh=False) or \
                         self.iconAtRoot(refreshPath,
                                         start)

                  return icon


          cherrypy.log('No link tag found:%s' % targetPath, severity=DEBUG)

      else:
        cherrypy.log('Non-success response:%d for url:%s' % \
                     (rootDomainPageResult.getcode(), targetPath),
                     severity=DEBUG)

    except:
      cherrypy.log('Error extracting favicon from page:%s, err:%s, msg:%s' % \
                   (targetPath, sys.exc_info()[0], sys.exc_info()[1]),
                   severity=DEBUG)

  def cacheIconLoc(self, domain, loc):
    cherrypy.log('Caching location:%s for domain:%s' % (loc, domain),
                 severity=DEBUG)

    if not self.mc.set('icon_loc-%s' % str(domain),
                       str(loc),
                       time=globals.MC_CACHE_TIME):
      cherrypy.log('Could not cache icon location for domain:%s' % domain,
                   severity=ERROR)

  def iconInCache(self, targetDomain, start):
    icon_loc = self.mc.get('icon_loc-%s' % targetDomain)
    if icon_loc:
      cherrypy.log('Cache hit:%s, location:%s' % (targetDomain, icon_loc),
                   severity=DEBUG)

      if icon_loc == globals.DEFAULT_FAVICON_LOC:
        self.mc.incr('counter-hits')
        self.mc.incr('counter-defaults')
        cherrypy.response.headers['X-Cache'] = 'Hit'
        return self.default_icon

      else:
        try:
          iconResult = self.open(icon_loc, start)
          icon = self.validateIconResponse(iconResult)
        except TimeoutError as e:
          cherrypy.log("TimeoutError: %s" % e, severity=ERROR)
          return None

        if icon:
          self.mc.incr('counter-hits')
          cherrypy.response.headers['X-Cache'] = 'Hit'
          icon.location = icon_loc
          return icon
        else:
          cherrypy.log('Cached location for domain:%s no longer valid' % \
                       targetDomain,
                       severity=INFO)

  def writeIcon(self, icon):
    self.writeHeaders(icon)
    return icon.data

  def writeHeaders(self, icon, fmt='%a, %d %b %Y %H:%M:%S %z'):
    # MIME Type
    cherrypy.response.headers['Content-Type'] = icon.type

    # Set caching headers
    cherrypy.response.headers['Cache-Control'] = 'public, max-age=2592000'
    cherrypy.response.headers['Expires'] = \
                          (datetime.now() + timedelta(days=30)).strftime(fmt)

  def parentLoc(self, url):
    urlPieces = urlparse.urlparse(self.urldecode(url))
    if not urlPieces or not urlPieces.scheme or not urlPieces.netloc:
      raise cherrypy.HTTPError(400, 'Malformed URL:%s' % url)

    parts = urlPieces.netloc.split('.')
    if len(parts) > 2:
      parent = '.'.join(parts[1:])
    else:
      parent = None

    cherrypy.log('domain:%s, parent:%s' % (urlPieces.netloc, parent),
                  severity=INFO)
    return parent

  def parse(self, url):
    # Get page path
    targetPath = self.urldecode(url)
    if not targetPath.startswith('http'):
      targetPath = 'http://%s' % targetPath
    cherrypy.log('Decoded URL:%s' % targetPath, severity=DEBUG)

    # Split path to get domain
    targetURL = urlparse.urlparse(targetPath)
    if not targetURL or not targetURL.scheme or not targetURL.netloc:
      raise cherrypy.HTTPError(400, 'Malformed URL:%s' % url)

    targetDomain = '%s://%s' % (targetURL.scheme, targetURL.netloc)
    cherrypy.log('URL:%s, domain:%s' % (targetPath, targetDomain),
                 severity=DEBUG)

    return (targetPath, targetDomain)

  @cherrypy.expose
  def index(self):
    status = {'status': 'ok', 'counters': dict()}
    for counter in ['requests', 'hits', 'defaults']:
      status['counters'][counter] = self.mc.get('counter-%s' %counter)
    return json.dumps(status)

  @cherrypy.expose
  def test(self):
    topSites = open(os.path.join(cherrypy.config['favicon.root'],
                                 'topsites.txt'), 'r').read().split()
    template = self.env.get_template('test.html')
    return template.render(topSites=topSites)

  @cherrypy.expose
  def clear(self, url):
    cherrypy.log('Incoming cache invalidation request:%s' % url,
                 severity=DEBUG)

    targetPath, targetDomain = self.parse(str(url))
    self.mc.delete('icon_loc-%s' % targetDomain)

    cherrypy.log('Evicted cache entry for %s' % targetDomain, severity=INFO)

  @cherrypy.expose
  def s(self, url, skipCache='false'):
    start = time()

    if skipCache.lower() == 'true':
      skipCache = True
    else:
      skipCache = False

    cherrypy.log('Incoming request:%s (skipCache=%s)' % (url, skipCache),
                 severity=DEBUG)

    self.mc.incr('counter-requests')

    targetPath, targetDomain = self.parse(str(url))

    #follow redirect for targetDomain -- ought to be in a separate function,
    #just like self.parse()
    redirectedPath, redirectedDomain = targetPath, targetDomain
    parentDomain = targetDomain
    temp_opener = urllib2.build_opener()
    try:
      temp_result = temp_opener.open(urllib2.Request(targetDomain, headers=globals.HEADERS),
                timeout=globals.CONNECTION_TIMEOUT)
      if temp_result.url:
        redirectedPath, redirectedDomain = self.parse(str(temp_result.url))
        parentDomain = self.parentLoc(temp_result.url)
        if parentDomain is None:
          parentDomain = targetDomain
    except Exception as e:
      cherrypy.log('Url:%s - failed to load/redirect because of %s' % (url,e),
                severity=WARN)
    #end redirect setup

    #extra lines from previous --
    #last line is for sites like blogger.com at the time of this writing
    icon = (not skipCache and self.iconInCache(redirectedDomain, start)) or \
           self.iconInPage(redirectedDomain, redirectedPath, start) or \
           self.iconAtRoot(redirectedDomain, start) or \
           self.iconAtRoot(parentDomain, start) or \
           self.iconAtRoot(targetDomain, start)

    if not icon:
      cherrypy.log('Falling back to default icon for:%s' % targetDomain,
                   severity=DEBUG)

      self.cacheIconLoc(targetDomain, globals.DEFAULT_FAVICON_LOC)
      self.mc.incr('counter-defaults')
      icon = self.default_icon

    cherrypy.log('Time taken to process domain:%s %f' % \
                 (targetDomain, time() - start),
                 severity=INFO)

    return self.writeIcon(icon)


if __name__ == '__main__':
  config = os.path.join(os.getcwd(), 'dev.conf')

  cherrypy.config.update(config)
  cherrypy.config.update({'favicon.root': os.getcwd()})
  stream = cherrypy.log.error_log.handlers[0]
  FORMATTER = Formatter(fmt="FILE:%(filename)-12s FUNC:%(funcName)-16s"
        + " LINE:%(lineno)-4s %(levelname)-8s %(message)s")
  stream.setFormatter(FORMATTER)
  cherrypy.log.error_log.setLevel(DEBUG)
  stream.setLevel(DEBUG)

  cherrypy.quickstart(PrintFavicon(), config=config)

# vim: sts=2:sw=2:ts=2
