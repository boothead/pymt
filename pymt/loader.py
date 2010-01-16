'''
Loader: asynchronous loader, easily extensible.

This is the Asynchronous Loader. You can use it to load an image
and use it, even if data are not yet available. You must specify a default
loading image for using a such loader ::

    from pymt import *
    image = Loader.image('mysprite.png')

You can also load image from url ::

    image = Loader.image('http://mysite.com/test.png')

If you want to change the default loading image, you can do ::

    Loader.loading_image = Image('another_loading.png')

'''

__all__ = ('Loader', 'LoaderBase', 'ProxyImage')

from pymt import pymt_data_dir
from pymt.logger import pymt_logger
from pymt.clock import getClock
from pymt.cache import Cache
from pymt.utils import SafeList
from pymt.core.image import ImageLoader, Image
from pymt.event import EventDispatcher
from abc import ABCMeta, abstractmethod

import time
import collections
import os

# Register a cache for loader
Cache.register('loader', limit=500)

class ProxyImage(Image, EventDispatcher):
    '''Image returned by the Loader.image() function.
    
    :Events:
        `on_load`
            Fired when the image is loaded and changed
    '''
    def __init__(self, arg, **kwargs):
        Image.__init__(self, arg, **kwargs)
        EventDispatcher.__init__(self)

        self.register_event_type('on_load')

    def on_load(self):
        pass


class LoaderBase(object):
    '''Common base for Loader and specific implementation.
    By default, Loader will be the best available loader implementation.

    The _update() function is called every 1 / 25.s or each frame if we have
    less than 25 FPS.
    '''

    __metaclass__ = ABCMeta

    def __init__(self):
        loading_png_fn = os.path.join(pymt_data_dir, 'loader.png')
        error_png_fn = os.path.join(pymt_data_dir, 'error.png')

        self.loading_image = ImageLoader.load(loading_png_fn)
        self.error_image = ImageLoader.load(error_png_fn)

        self._q_load  = collections.deque()
        self._q_done  = collections.deque()
        self._client  = SafeList()
        self._running = False
        self._start_wanted = False

        getClock().schedule_interval(self._update, 1 / 25.)

    def __del__(self):
        try:
            getClock().unschedule_intervale(self._update)
        except:
            pass

    @abstractmethod
    def start(self):
        '''Start the loader thread/process'''
        self._running = True

    @abstractmethod
    def run(self, *largs):
        '''Main loop for the loader.'''
        pass

    @abstractmethod
    def stop(self):
        '''Stop the loader thread/process'''
        self._running = False

    def _load(self, parameters):
        '''(internal) Loading function, called by the thread.
        Will call _load_local() if the file is local,
        or _load_urllib() if the file is on Internet'''

        filename, tcallback = parameters
        proto = filename.split(':', 1)[0]
        if proto in ('http', 'https', 'ftp'):
            data = self._load_urllib(filename)
        else:
            data = self._load_local(filename)

        if tcallback:
            data = tcallback(data)

        self._q_done.append((filename, data))

    def _load_local(self, filename):
        '''(internal) Loading a local file'''
        return ImageLoader.load(filename)

    def _load_urllib(self, filename):
        '''(internal) Loading a network file. First download it, save it to a
        temporary file, and pass it to _load_local()'''
        import urllib2, tempfile
        data = None
        try:
            suffix = '.%s'  % (filename.split('.')[-1])
            _out_osfd, _out_filename = tempfile.mkstemp(
                    prefix='pymtloader', suffix=suffix)

            # read from internet
            fd = urllib2.urlopen(filename)
            idata = fd.read()
            fd.close()

            # write to local filename
            fd = open(_out_filename, 'wb')
            fd.write(idata)
            fd.close()

            # load data
            data = self._load_local(_out_filename)
        except:
            pymt_logger.exception('Failed to load image <%s>' % filename)
            return self.error_image
        finally:
            os.unlink(_out_filename)

        return data

    def _update(self, *largs):
        '''(internal) Check if a data is loaded, and pass to the client'''
        # want to start it ?
        if self._start_wanted:
            if not self._running:
                self.start()
            self._start_wanted = False

        while True:
            try:
                filename, data = self._q_done.pop()
            except:
                return

            # create the image
            image = ProxyImage(data)
            Cache.append('loader', filename, image)

            # update client
            for c_filename, client in self._client.iterate():
                if filename != c_filename:
                    continue
                # got one client to update
                client.image = image
                client.dispatch_event('on_load')
                self._client.remove((c_filename, client))

    def image(self, filename, tcallback=None):
        '''Load a image using loader. A Proxy image is returned
        with a loading image ::
        
            img = Loader.image(filename)
            # img will be a ProxyImage.
            # You'll use it the same as an Image class.
            # Later, when the image is really loaded,
            # the loader will change the img.image property
            # to the new loaded image

        '''
        data = Cache.get('loader', filename)
        if data not in (None, False):
            # found image
            return ProxyImage(data,
                    loading_image=self.loading_image)

        client = ProxyImage(self.loading_image,
                    loading_image=self.loading_image)
        self._client.append((filename, client))

        if data is None:
            # if data is None, this is really the first time
            self._q_load.append((filename, tcallback))
            Cache.append('loader', filename, False)
            self._start_wanted = True
        else:
            # already queued for loading
            pass

        return client

#
# Loader implementation
#

if 'PYMT_DOC' in os.environ:

    Loader = None

else:

    #
    # Try to use pygame as our first choice for loader
    #

    try:
        import pygame

        class LoaderPygame(LoaderBase):
            def start(self):
                super(LoaderPygame, self).start()
                self.worker = pygame.threads.WorkerQueue()
                self.worker.do(self.run)

            def stop(self):
                super(LoaderPygame, self).stop()
                self.worker.stop()

            def run(self, *largs):
                while self._running:
                    try:
                        parameters = self._q_load.pop()
                    except:
                        time.sleep(0.1)
                        continue
                    self.worker.do(self._load, parameters)

        Loader = LoaderPygame()
        pymt_logger.info('Loader: using <pygame> as thread loader')

    except:

        #
        # Default to the clock loader
        #

        class LoaderClock(LoaderBase):
            '''Loader implementation using a simple Clock()'''
            def start(self):
                super(LoaderClock, self).start()
                getClock().schedule_interval(self.run, 0.0001)

            def stop(self):
                super(LoaderClock, self).stop()
                getClock().unschedule_interval(self.run)

            def run(self, *largs):
                try:
                    parameters = self._q_load.pop()
                except:
                    return
                self._load(parameters)

        Loader = LoaderClock()
        pymt_logger.info('Loader: using <clock> as thread loader')

