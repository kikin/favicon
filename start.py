import os
import sys

import atexit
import threading
import cherrypy

sys.path.append('/opt/favicon_env/src')
sys.path.append('/usr/local/lib/python2.6/dist-packages')

import favicon

sys.stdout = sys.stderr
cherrypy.config.update({'environment': 'embedded',
                        'log.screen': True})

if cherrypy.__version__.startswith('3.0') and cherrypy.engine.state == 0:
    cherrypy.engine.start(blocking=False)
    atexit.register(cherrypy.engine.stop)

application = cherrypy.Application(favicon.PrintFavicon(), script_name=None, config=None)
