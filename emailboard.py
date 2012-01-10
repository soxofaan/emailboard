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

# TODO: prune old emails automatically?

import os
import BaseHTTPServer
import logging
import threading
import smtpd
import asyncore
import re
import sqlite3
import time
import datetime
import email




# Threading lock for db creation.
_get_database_connection_lock = threading.Lock()


def get_database_connection(db_file_name):
    '''
    Get a connection to the SQLite database file
    and create it when not available yet.
    '''
    log = logging.getLogger('emailboard.db')
    log.debug('Getting database connection from file ' + db_file_name)

    global _get_database_connection_lock
    _get_database_connection_lock.acquire()
    if not os.path.exists(db_file_name):
        log.debug('Creating database in file "{0}".'.format(db_file_name))
        conn = sqlite3.connect(db_file_name)
        c = conn.cursor()
        # TODO: check sender field size
        # TODO: check receiver field size
        # TODO: index on receivers?
        # TODO: index on subject?
        c.execute('''
            CREATE TABLE emails (
                id INTEGER PRIMARY KEY,
                timestamp INTEGER UNSIGNED,
                sender VARCHAR(64),
                receivers VARCHAR(512),
                subject VARCHAR(256),
                data TEXT
            )
        ''')
        conn.commit()
    _get_database_connection_lock.release()

    connection = sqlite3.connect(db_file_name)
    return connection








class HttpRequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    '''
    Simple handler for HTTP requests.
    '''

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
        # TODO: paging
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        # Send content.
        self.wfile.write('<html><body>')
        self.wfile.write('listing:<ol>')
        c = self.server.db_connection.cursor()
        c.execute('SELECT id, timestamp, sender, receivers, subject, data FROM emails')
        for (id, timestamp, sender, receivers, subject, data) in c.fetchall():
            self.wfile.write('<li><a href="/{i}">{sender}: {subject}, {date}</a></li>'.format(i=id, sender=sender, subject=subject, date=time.ctime(timestamp)))
        self.wfile.write('</ol>')
        self.wfile.write('</body></html>')

    def do_show_email(self, id):
        # TODO: option to switch between raw/text/html view
        # Get entry.
        c = self.server.db_connection.cursor()
        c.execute('SELECT id, timestamp, sender, receivers, subject, data FROM emails WHERE id=?', (id,))
        (id, timestamp, sender, receivers, subject, data) = c.fetchone()
        # Render.
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(data)

    def do_404(self):
        # Send header.
        self.send_response(404)
        self.end_headers()
        # Send content.
        self.wfile.write('<htm><body>page not found: {0!r}</body></html>'.format(self.path))


class HttpServerThread(threading.Thread):
    '''
    HTTP server thread.
    '''

    log = logging.getLogger('emailboard.httpdthread')

    def __init__(self, server_address, db_file):
        threading.Thread.__init__(self)
        self.daemon = True
        self.server_address = server_address
        self._db_file = db_file

    def run(self):
        # Create HTTP server.
        httpd = BaseHTTPServer.HTTPServer(
            server_address=self.server_address,
            RequestHandlerClass=HttpRequestHandler
        )
        # Store a reusable database connection, to be used by request handler.
        httpd.db_connection = get_database_connection(self._db_file)
        # Start it
        self.log.debug('Starting server ({0!r}'.format(self.server_address))
        httpd.serve_forever()


class SmtpServer(smtpd.SMTPServer):
    '''
    Handler for SMTP requests.
    '''

    log = logging.getLogger('emailboard.smtpd')

    def __init__(self, localaddr, db_file):
        smtpd.SMTPServer.__init__(self, localaddr, remoteaddr=None)
        # Reusable database connection.
        self.db_connection = get_database_connection(db_file)

    def process_message(self, peer, mailfrom, rcpttos, data):
        self.log.debug('Received message from peer {0}'.format(peer))
        self.log.debug('Message addressed from {0}'.format(mailfrom))
        self.log.debug('Message addressed to {0!r}'.format(rcpttos))
        self.log.debug('Message body (first part): {0}'.format(data[:1000]))
        # Store email.
        c = self.db_connection.cursor()
        timestamp = int(time.time())
        msg = email.message_from_string(data)
        subject = msg['Subject']
        c.execute('''
            INSERT INTO emails (timestamp, sender, receivers, subject, data)
            VALUES (?,?,?,?,?)''', (timestamp, mailfrom, ','.join(rcpttos), subject, data))
        self.db_connection.commit()


class SmtpServerThread(threading.Thread):
    '''
    SMTP server thread.
    '''

    log = logging.getLogger('emailboard.smtpdthread')

    def __init__(self, server_address, db_file):
        threading.Thread.__init__(self)
        self.daemon = True
        self.server_address = server_address
        self._db_file = db_file

    def run(self):
        self.log.debug('Setting up SMTP server on {0}'.format(self.server_address))
        self._server = SmtpServer(self.server_address, self._db_file)
        # Start asyncore loop
        asyncore.loop()


def main():
    # TODO: provide command line option to set server name and port numbers
    # TODO: provide command line option to set logging level

    log = logging.getLogger('emailboard')

    # Emailboard database file
    # TODO: provide config value to set this file?
    db_file = 'emailboard.sqlite'

    # HTTP server
    log.info('Starting HTTP server thread')
    httpd_thread = HttpServerThread(server_address=('localhost', 8989), db_file=db_file)
    httpd_thread.start()

    # SMTP server
    log.info('Starting SMTP server thread')
    smtpd_thread = SmtpServerThread(server_address=('localhost', 9898), db_file=db_file)
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
    except KeyboardInterrupt:
        log.info('Received keyboard interrupt: closing down.')
        return


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    main()
