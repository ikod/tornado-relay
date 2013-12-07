#!/usr/bin/env python

__author__ = 'igor'

import socket
import logging
import struct
import time
import sys
import signal
import tornado.ioloop
import tornado.iostream
import tornado.process
import tornado.netutil

from tornado.options import options, define, parse_command_line, parse_config_file
from collections import deque

from cPickle import loads, dumps

from carbon.routers import ConsistentHashingRouter, RelayRulesRouter

from tornado import gen

BACKLOG = 100
RECONNECT_INTERVAL = 10

define("line_port", type=int, help="port for line interface", default=2013)
define("pickle_port", type=int, help="port for pickle interface", default=2014)
define("processes", type=int, help="num of parallel processes", default=2)
define("destinations", type=str, help="carbon DESTINATIONS list, comma separated, in format hostname:port:instance",
       default=None)
define("replication", type=int, help="replication factor", default=1)
define("rules", type=str, help="carbon rules file", default=None)
define("instance", type=str, help="instance name", default="a")
define("maxqlen", type=int, help="outpuit queue length", default=100000)
define("config", type=str, help="path to config file", default=None)

parse_command_line()
if options.config:
    parse_config_file(options.config)

if not options.destinations:
    print "Can't run without --destinations"
    sys.exit(1)


def mysleep(interval, callback):
    tornado.ioloop.IOLoop.instance().add_timeout(time.time() + interval, callback)


class Destination(object):
    __slots__ = ('name', 'addr', 'socket', 'state', 'stream', 'queue', 'overflows', 'last_reconnect', 'inxmit')

    DISCONNECTED = 0
    CONNECTED = 1

    def __init__(self, n):
        """

        @type self: Destination
        @param n: destination name
        @type n: str
        """
        self.name = n
        self.state = Destination.DISCONNECTED
        self.queue = deque([], options.maxqlen)
        self.overflows = 0
        self.last_reconnect = 0
        self.socket = None
        self.stream = None
        self.inxmit = False
        self.addr = (self.name.split(':')[0], int(self.name.split(':')[1]))
        logger.debug(self.name + ' destination created')

        self.start_connection()

    def stream_closed(self):
        logger.debug(self.name + ' disconnected')
        self.state = Destination.DISCONNECTED
        self.inxmit = False
        tornado.ioloop.IOLoop.instance().add_timeout(time.time() + RECONNECT_INTERVAL, self.start_connection)

    @gen.engine
    def start_connection(self):
        """
        @type self: Destination
        """
        logger.debug(self.name + ' start connection')
        assert self.state == Destination.DISCONNECTED
        self.socket = socket.socket()
        self.stream = tornado.iostream.IOStream(self.socket)
        self.stream.set_close_callback(self.stream_closed)
        yield gen.Task(self.stream.connect, self.addr)
        if not self.stream.error:
            logger.info(self.name + ' connected')
            self.state = Destination.CONNECTED
            # connected, start sending queued data
            yield gen.Task(self.start_sendq)
        else:
            logger.error(self.name + ' can''t connect')
            self.state = Destination.DISCONNECTED

    @gen.engine
    def start_sendq(self, callback=None):
        if self.inxmit:
            if callback:
                callback()
            return
        self.inxmit = True
        logger.debug(self.name + ' sending ' + str(self.queue))
        while self.state == Destination.CONNECTED and len(self.queue) > 0:
            # start transmission
            data = self.queue.popleft()
            length = len(data)
            header = struct.pack('!L', length)
            yield gen.Task(self.stream.write, header + data)
        self.inxmit = False
        if callback:
            callback()

    def __str__(self):
        return self.name


class LineHandler(object):
    def __init__(self, router, destinations):
        """
        @type router: carbon.routers.ConsistentHashingRouter
        @type destinations: dict
        """
        self.router = router
        self.destinations = destinations

    @gen.engine
    def __call__(self, s, peer):
        """
        @type s: socket.socket
        @type peer: str
        """
        stream = tornado.iostream.IOStream(s)
        logger.debug('line socket accepted from '+str(peer))
        try:
            router = self.router
            destinations = self.destinations
            while not stream.closed():
                line_data = yield gen.Task(stream.read_until, '\n')
                logger.debug("got " + line_data.strip())
                # route data to destinations
                batches = dict()
                splitted = line_data.strip().split()
                data = (splitted[0], (splitted[2], splitted[1]))

                if len(destinations.keys()) == router.replication_factor:
                    for dest in destinations.keys():
                        batches[dest] = [data]
                else:
                    metric = splitted[0]
                    for dest in self.router.getDestinations(metric):
                        dest_key = ':'.join(dest)
                        if dest_key in batches:
                            batches[dest_key].append(data)
                        else:
                            batches[dest_key] = [data]
                for key, data in batches.items():
                    destination = self.destinations[key]
                    if len(destination.queue) >= options.maxqlen:
                        destination.overflows += 1
                    destination.queue.append(dumps(data))
                    yield gen.Task(destination.start_sendq)
        except:
            logger.error(sys.exc_info())
        finally:
            stream.close()


class PickleHandler(object):
    def __init__(self, router, destinations):
        """
        @type router: carbon.routers.ConsistentHashingRouter
        @type destinations: dict
        """
        self.router = router
        self.destinations = destinations

    @gen.engine
    def __call__(self, s, peer):
        """
        @type s: socket.socket
        @type peer: str
        """
        logger.debug('pickle socket accepted')
        stream = tornado.iostream.IOStream(s)

        while not stream.closed():
            header = yield gen.Task(stream.read_bytes, struct.calcsize('!L'))
            msglen = struct.unpack('!L', header)[0]
            logger.debug('have to read ' + str(msglen) + ' bytes from ' + str(peer))
            data = yield gen.Task(stream.read_bytes, msglen)
            router = self.router
            destinations = self.destinations

            # route data to destinations
            batches = dict()
            if len(destinations.keys()) == router.replication_factor:
                for dest in destinations.keys():
                    batches[dest] = data
            else:
                for metric, value in loads(data):
                    for dest in self.router.getDestinations(metric):
                        dest_key = ':'.join(dest)
                        if dest_key in batches:
                            batches[dest_key].append((metric, value))
                        else:
                            batches[dest_key] = [(metric, value)]
                for (destination, data) in batches.items():
                    batches[destination] = dumps(data)

            for key, data in batches.items():
                destination = self.destinations[key]
                if len(destination.queue) >= options.maxqlen:
                    destination.overflows += 1
                destination.queue.append(data)
                yield gen.Task(destination.start_sendq)


def main(opts):
    assert opts.destinations
    assert opts.instance
    line_port = int(opts.line_port)
    pickle_port = int(opts.pickle_port)
    processes = int(opts.processes)
    destinations = opts.destinations.split(',')
    replication_factor = opts.replication

    line_socket = socket.socket()
    line_socket.setblocking(0)
    line_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    line_socket.bind(('0.0.0.0', line_port))
    line_socket.listen(BACKLOG)
    pickle_socket = socket.socket()
    pickle_socket.setblocking(0)
    pickle_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    pickle_socket.bind(('0.0.0.0', pickle_port))
    pickle_socket.listen(BACKLOG)

    tornado.process.fork_processes(processes)

    if options.rules:
        router = RelayRulesRouter(options.rules)
    else:
        router = ConsistentHashingRouter(replication_factor=replication_factor)

    DESTINATIONS = dict()
    # create destinations
    for destination in destinations:
        DESTINATIONS[destination] = Destination(destination)
        router.addDestination(destination.split(':'))

    tornado.netutil.add_accept_handler(line_socket, LineHandler(router, DESTINATIONS))
    tornado.netutil.add_accept_handler(pickle_socket, PickleHandler(router, DESTINATIONS))

    tornado.ioloop.IOLoop.instance().start()


def signal_handler(signum, frame):
    """
    @param signum: int
    @param frame: frame
    """
    tornado.ioloop.IOLoop.instance().stop()


if __name__ == '__main__':
    logger = logging.getLogger()
    signal.signal(signal.SIGINT, signal_handler)
    main(options)
