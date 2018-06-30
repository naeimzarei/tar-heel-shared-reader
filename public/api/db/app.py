#!/usr/bin/python3
'''
A simple db for Tar Heel Shared Reader
'''

import bottle
from bottle import Bottle, request, response
from datetime import datetime
from db import with_db, insert
import os.path as osp

app = application = Bottle()

# enable debugging
bottle.debug(True)

# cookie signing
secret = 'Salt and light'


def static_path(filename):
    '''
    Produce the path to a static file
    '''
    p = osp.join('./static', filename)
    m = osp.getmtime(p)
    s = '%x' % int(m)
    u = app.get_url('static', filename=filename)
    return u + '?' + s


bottle.SimpleTemplate.defaults['static'] = static_path


@app.route('/static/<filename:path>', name='static')
def static(filename):
    '''
    Serve static files in development
    '''
    return bottle.static_file(filename, root='./static')


# simple minded security
def user_is_known(username, password=None):
    '''The user has logged in'''
    return username


def user_is_admin(username, password=None):
    '''The user is authorized'''
    return username in ['gb']


def user_is_me(username, password=None):
    '''The user is admin'''
    return username == 'gb'


def get_user():
    user = request.get_cookie('user', secret=secret)
    return user


def set_user(user):
    response.set_cookie('user', user, secret=secret)


def auth(check):
    '''decorator to apply above functions for auth'''
    def decorator(function):
        def wrapper(*args, **kwargs):
            user = get_user()
            if not user:
                path = app.get_url('root') + request.path[1:]
                bottle.redirect(app.get_url('login') + '?path=' + path)
            elif not check(user):
                raise bottle.HTTPError(403, 'Forbidden')
            return function(*args, **kwargs)
        return wrapper
    return decorator


def allow_json(func):
    ''' Decorator: renders as json if requested '''
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        if ('application/json' in request.header.get('Accept') and
                isinstance(result, dict)):
            return bottle.HTTPResponse(result)
        return result
    return wrapper


def eprint(*args, **kwargs):
    from time import time
    import sys
    global t0
    if t0 is None:
        t0 = time()
    print(time() - t0, *args, file=sys.stderr, **kwargs)
    sys.stderr.flush()


@app.route('/students')
@with_db
def students(db):
    '''
    return a list of student ids
    '''
    result = db.execute('''
        select distinct student from log
          where teacher = ? and student != ''
          order by student
    ''', [request.query.get('teacher')]).fetchall()
    return {'students': [r['student'] for r in result]}


@app.route('/students', method='POST')
@with_db
def addStudent(db):
    '''
    Add a students for this teacher
    '''
    data = request.json
    teacher, student = data['teacher'], data['student']
    db.execute('''
        insert into log
            (time, teacher, student, action) values (?, ?, ?, 'add')
    ''', [datetime.now(), teacher, student]).fetchall()
    return 'ok'


@app.route('/books')
@with_db
def getBooksIndex(db):
    '''
    List all books
    '''
    teacher = request.query.get('teacher')
    response = {'recent': [], 'yours': [], 'books': []}
    if teacher:
        # 8 most recently read books
        recent = db.execute('''
            select B.title, B.author, B.pages, S.slug, S.level, B.image
            from books B, shared S
            where B.bookid = S.bookid and
              S.status = 'published' and S.slug in
                (select distinct slug from log
                 where teacher = ?
                 order by time desc
                 limit 8)
        ''', [teacher]).fetchall()
        response['recent'] = recent
        # books owned by this teacher
        yours = db.execute('''
            select B.title, B.author, B.pages, S.slug, S.level, B.image
            from books B, shared S
            where B.bookid = S.bookid and
                S.status in ('published', 'draft') and
                S.owner = ?
        ''', [teacher]).fetchall()
        response['yours'] = yours
    else:
        results = db.execute('''
            select B.title, B.author, B.pages, S.slug, S.level, B.image
            from books B, shared S
            where B.bookid = S.bookid and
                S.status = 'published'
        ''').fetchall()
        response['books'] = results
    return response


@app.route('/books/:slug')
@with_db
def getBook(db, slug):
    '''
    Return json for a book
    '''
    book = db.execute('''
        select B.title, S.slug, S.status, S.level, B.author, S.owner,
            S.sharedid, B.bookid
        from books B, shared S
        where B.bookid = S.bookid and S.slug = ?
    ''', [slug]).fetchone()
    pages = db.execute('''
        select caption as text, image as url, width, height
        from pages
        where bookid = ?
        order by pageno
    ''', [book['bookid']]).fetchall()
    comments = db.execute('''
        select comment, pageno from comments
        where sharedid = ?
        order by pageno, reading
    ''', [book['sharedid']]).fetchall()
    for pageno, page in enumerate(pages):
        page['comments'] = [
            comment['comment']
            for comment in comments
            if comment['pageno'] == pageno
        ]
    book['pages'] = pages
    return book


@app.route('/log', method='POST')
@with_db
def log(db):
    '''
    Add a record to the log
    '''
    d = request.json
    # get the actual comment
    if d['bookid']:
        comment = db.execute('''
            select C.comment from shared S, comments C
                where S.slug = ? and S.sharedid = C.sharedid and
                    C.pageno = ? and C.reading = ?
        ''', (d['bookid'], d['page'], d['reading'])).fetchone()['comment']
        d['comment'] = comment
    d['time'] = datetime.now()
    d['slug'] = d['bookid']
    del d['bookid']
    insert(db, 'log', **d)
    return 'ok'


class StripPathMiddleware(object):
    '''
    Get that slash out of the request
    '''
    def __init__(self, a):
        self.a = a

    def __call__(self, e, h):
        e['PATH_INFO'] = e['PATH_INFO'].rstrip('/')
        return self.a(e, h)


if __name__ == '__main__':
    bottle.run(
        app=StripPathMiddleware(app),
        reloader=True,
        debug=True,
        host='localhost',
        port=5500)
