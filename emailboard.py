#!/usr/bin/env python
'''
EmailBoard
Copyright (C) 2012 Stefaan Lippens

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
'''


import sys
import BaseHTTPServer
import logging
import threading
import signal


class EmailBoardHandler(BaseHTTPServer.BaseHTTPRequestHandler):

    def do_GET(self):
        logging.debug('self.path: ' + self.path)
        # Send header.
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        # Send content.
        self.wfile.write('You asked ' + self.path)


class HttpServerThread(threading.Thread):

    def __init__(self, server_address):
        threading.Thread.__init__(self)
        self.daemon = True
        self.server_address = server_address

    def run(self):
        # Create HTTP server.
        httpd = BaseHTTPServer.HTTPServer(
            server_address=self.server_address,
            RequestHandlerClass=EmailBoardHandler
        )
        # Start it
        logging.debug('Starting server ({0!r}'.format(self.server_address))
        httpd.serve_forever()


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)

    logging.debug('Starting HTTP server thread')
    httpd_thread = HttpServerThread(server_address=('localhost', 8989))
    httpd_thread.start()

    logging.debug('Waiting for threads to stop.')

    # Just calling httpd_thread.join() does not play well
    # with threading and KeyboardInterrupts. Calling join with a timeout
    # (in a loop) seems to do it better
    while httpd_thread.isAlive():
        logging.debug('Still alive.')
        httpd_thread.join(10)

    logging.debug('That\'s all folks')
