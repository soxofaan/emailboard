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
import logging
import threading
import smtpd
import asyncore
import re
import sqlite3
import time
import datetime
import email
import wsgiref.simple_server




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


#############################################################################
# Web app implementation and serving

class NotFoundException(Exception):
    '''Exception for page not found situations.'''
    pass


class RedirectException(Exception):
    '''Exception to raise for HTTP redirects.'''
    def __init__(self, target):
        Exception.__init__(self)
        self.target = target


class WebAppResponse(object):
    '''Simple wrapper for web app responses, with some defaults.'''
    def __init__(self, content, content_type='text/html', status='200 OK'):
        self.content = str(content)
        self.headers = [('Content-type', content_type)]
        self.status = status


class WebApp:
    '''
    ReviewBoard WSGI web app.

    Implemented as WSGI app with standard lib functionality (no external web frameworks).
    '''

    log = logging.getLogger('emailboard.webapp')

    def __init__(self, db_file):
        # Store reusable DB connection.
        self.db_connection = get_database_connection(db_file)
        # Set up Routing: list of tuples (path_pattern, method, callback)
        self._routing = [
            ('^/$', 'GET', self.get_listing),
            ('^/(?P<id>[0-9]+)/?$', 'GET', self.get_email),
        ]

    def __call__(self, environ, start_response):
        '''WSGI callable implementation.'''

        request_method = environ['REQUEST_METHOD']
        path_info = environ['PATH_INFO']
        self.log.debug('Request for path "%s" (%s)' % (path_info, request_method))

        try:
            for path_pattern, method, callback in self._routing:
                if method != request_method:
                    continue
                m = re.match(path_pattern, path_info)
                if m == None:
                    continue
                # Extract arguments from parsed path as a dictionary.
                arg_dict = m.groupdict()
                self.log.debug('Dispatching request to callback "{callback}" with argument dict {arg_dict}'.format(callback=callback, arg_dict=arg_dict))
                response = callback(environ, **arg_dict)
                break
            else:
                raise NotFoundException()
        except NotFoundException:
            response = WebAppResponse('Not found: %s' % environ['PATH_INFO'], content_type='text/plain', status='404 Not Found')
        except RedirectException, e:
            response = WebAppResponse('', status='301 Moved Permanently')
            response.headers.append(('Location', e.location))
        except Exception, e:
            self.log.error('Exception occured: ' + repr(e))
            response = WebAppResponse('Internal server error', content_type='text/plain', status='500 Internal Server Error')

        # WSGI API: call start_response and return content.
        start_response(response.status, response.headers)
        return [response.content]

    def get_listing(self, environ):
        # Send content.
        content = '<html><body>'
        content += 'listing:<ol>'
        c = self.db_connection.cursor()
        c.execute('SELECT id, timestamp, sender, receivers, subject, data FROM emails')
        for (id, timestamp, sender, receivers, subject, data) in c.fetchall():
            content += '<li><a href="/{i}">{sender}: {subject}, {date}</a></li>'.format(i=id, sender=sender, subject=subject, date=time.ctime(timestamp))
        content += '</ol>'
        content += '</body></html>'
        return WebAppResponse(content)

    def get_email(self, environ, id):
        id = int(id)
        # TODO: option to switch between raw/text/html view
        # Get entry.
        c = self.db_connection.cursor()
        c.execute('SELECT id, timestamp, sender, receivers, subject, data FROM emails WHERE id=?', (id,))
        (id, timestamp, sender, receivers, subject, data) = c.fetchone()
        # Render.
        return WebAppResponse(content=data, content_type='text/plain')


class WebAppServerThread(threading.Thread):
    '''
    Web app (HTTP) server thread.
    '''

    log = logging.getLogger('emailboard.httpdthread')

    def __init__(self, host, port, db_file):
        threading.Thread.__init__(self)
        self.daemon = True
        self.server_host = host
        self.server_port = port
        self._db_file = db_file

    def run(self):
        httpd = wsgiref.simple_server.make_server(self.server_host, self.server_port, WebApp(self._db_file))
        self.log.debug('Starting server (%s:%d)' % (self.server_host, self.server_port))
        httpd.serve_forever()


#############################################################################
# SMTP server

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
    log.info('Starting web app server thread')
    httpd_thread = WebAppServerThread('localhost', 8989, db_file=db_file)
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
