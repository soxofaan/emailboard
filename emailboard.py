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


import BaseHTTPServer
import logging
import threading
import smtpd
import asyncore
import re

# Poor man's database
db_lock = threading.Lock()
db = []


class HttpRequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):

    log = logging.getLogger('emailboard.requesthandler')

    def do_GET(self):
        self.log.debug('self.path: ' + self.path)
        if self.path == '/':
            self.do_listing()
        elif re.match('/[0-9]+', self.path):
            self.do_show_email(int(self.path[1:]))
        else:
            self.do_404()

    def do_listing(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        # Send content.
        self.wfile.write('<html><body>')
        self.wfile.write('listing:<ol>')
        global db_lock, db
        db_lock.acquire()
        for i, entry in enumerate(db):
            self.wfile.write('<li>{0!r}</li>'.format(entry))
        db_lock.release()
        self.wfile.write('</ol>')
        self.wfile.write('</body></html>')

    def do_show_email(self, id):
        pass

    def do_404(self):
        # Send header.
        self.send_response(404)
        self.end_headers()
        # Send content.
        self.wfile.write('<htm><body>page not found: {0!r}</body></html>'.format(self.path))



class HttpServerThread(threading.Thread):

    log = logging.getLogger('emailboard.httpdthread')

    def __init__(self, server_address):
        threading.Thread.__init__(self)
        self.daemon = True
        self.server_address = server_address

    def run(self):
        # Create HTTP server.
        httpd = BaseHTTPServer.HTTPServer(
            server_address=self.server_address,
            RequestHandlerClass=HttpRequestHandler
        )
        # Start it
        self.log.debug('Starting server ({0!r}'.format(self.server_address))
        httpd.serve_forever()


class SmtpServer(smtpd.SMTPServer):

    log = logging.getLogger('emailboard.smtpd')

    def process_message(self, peer, mailfrom, rcpttos, data):
        self.log.debug('Received message from peer {0}'.format(peer))
        self.log.debug('Message addressed from {0}'.format(mailfrom))
        self.log.debug('Message addressed to {0!r}'.format(rcpttos))
        self.log.debug('Message body (first part): {0}'.format(data[:1000]))
        global db_lock, db
        db_lock.acquire()
        db.append((peer, mailfrom, rcpttos, data))
        db_lock.release()


class SmtpServerThread(threading.Thread):

    log = logging.getLogger('emailboard.smtpdthread')

    def __init__(self, server_address):
        threading.Thread.__init__(self)
        self.daemon = True
        self.server_address = server_address

    def run(self):
        self.log.debug('Setting up SMTP server on {0}'.format(self.server_address))
        server = SmtpServer(self.server_address, None)
        # Start asyncore loop
        asyncore.loop()

def main():
    log = logging.getLogger('emailboard')

    # HTTP server
    log.info('Starting HTTP server thread')
    httpd_thread = HttpServerThread(server_address=('localhost', 8989))
    httpd_thread.start()

    # SMTP server
    log.info('Starting SMTP server thread')
    smtpd_thread = SmtpServerThread(server_address=('localhost', 9898))
    smtpd_thread.start()

    log.info('Monitoring the working threads.')
    # Just calling httpd_thread.join() does not play well
    # with threading and KeyboardInterrupts. Calling join with a timeout
    # (in a loop) seems to do it better.
    try:
        while httpd_thread.isAlive() and smtpd_thread.isAlive():
            log.debug('Threads alive? HTTPD: {0}, SMTPD: {0}.'.format(httpd_thread.isAlive(), smtpd_thread.isAlive()))
            httpd_thread.join(10)
            smtpd_thread.join(10)
        if not httpd_thread.isAlive():
            log.error('HTTP server thread died unexpectedly. There must be something wrong.')
        if not smtpd_thread.isAlive():
            log.error('SMTP server thread died unexpectedly. There must be something wrong.')
    except KeyboardInterrupt, e:
        log.info('Received keyboard interrupt: closing down.')
        return


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    main()
