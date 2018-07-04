#!/usr/bin/python3
'''
A simple db for Tar Heel Shared Reader
'''

import bottle
from bottle import Bottle, request, HTTPError
from datetime import datetime
from db import with_db, insert
import os.path as osp
import urllib.request
import json
import re

app = application = Bottle()

# enable debugging
bottle.debug(True)

# cookie signing
secret = 'Salt and light'

# THR = 'https://tarheelreader.org/'
THR = 'https://gbserver3.cs.unc.edu/'


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


AUTH_KEY = 'S=n[!1BFy j@#_iiunJmNrGtf@]}5E>EVU ?Tt-CWK<>\\@5el!3r_vn/M=vU&/(N'


roles = {
    'admin': 3,
    'author': 2,
    'participant': 1
}


def auth(min_role):
    '''Validate auth and add user and role arguments'''
    def decorator(func):
        def func_wrapper(*args, **kwargs):
            try:
                # parse the authentication header
                ah = request.headers.get('Authentication', '')
                m = re.match(
                        r'^MYAUTH '
                        r'user:"([a-zA-Z0-9 ]+)", '
                        r'role:"([a-z]+)", '
                        r'token:"([0-9a-f]+)"',
                        ah)
                if not m:
                    raise HTTPError
                # validate the user
                name, role, token = m.groups()
                url = THR + 'login?{}'.format(urllib.parse.urlencode({
                    'shared': 2,
                    'login': name,
                    'role': role,
                    'hash': token
                    }))
                r = urllib.request.urlopen(url).read()
                resp = json.loads(r.decode('utf-8'))
                if not resp['ok']:
                    raise HTTPError
                # check the role
                if roles.get(role, 0) < roles[min_role]:
                    raise HTTPError
            except HTTPError:
                raise HTTPError(403, 'Forbidden')
            return func(*args, **dict(kwargs, user=name, role=role))

        return func_wrapper
    return decorator


@app.route('/students')
@with_db
@auth('participant')
def students(db, user, role):
    '''
    return a list of student ids
    '''
    result = db.execute('''
        select distinct student from log
          where teacher = ? and student != ''
          order by student
    ''', [user]).fetchall()
    return {'students': [r['student'] for r in result]}


@app.route('/students', method='POST')
@with_db
@auth('participant')
def addStudent(db, user, role):
    '''
    Add a students for this teacher
    '''
    data = request.json
    teacher, student = user, data['student']
    insert(db, 'log', time=datetime.now(), teacher=teacher,
           student=student, action='add')
    return 'ok'


@app.route('/books')
@with_db
def getBooksIndex(db):
    '''
    List all books
    '''
    teacher = request.query.get('teacher')
    result = {'recent': [], 'yours': [], 'books': []}
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
        result['recent'] = recent
        # books owned by this teacher
        yours = db.execute('''
            select B.title, B.author, B.pages, S.slug, S.level, B.image
            from books B, shared S
            where B.bookid = S.bookid and
                S.status in ('published', 'draft') and
                S.owner = ?
        ''', [teacher]).fetchall()
        result['yours'] = yours
    else:
        results = db.execute('''
            select B.title, B.author, B.pages, S.slug, S.level, B.image
            from books B, shared S
            where B.bookid = S.bookid and
                S.status = 'published'
        ''').fetchall()
        result['books'] = results
    return result


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


@app.route('/books', method='POST')
@with_db
def newBook(db):
    '''
    Create a new book
    '''
    data = request.json
    thrslug = data['thrslug']
    teacher = 'admin'  # FIX ME
    # get the book content from THR
    r = urllib.request.urlopen(THR + 'book-as-json?slug=%s' % thrslug).read()
    b = json.loads(r.decode('utf-8'))
    # add the content to our tables
    c = insert(db, 'books',
               thrslug=thrslug, title=b['title'], author=b['author'],
               image=b['pages'][0]['url'], pages=len(b['pages']))
    bookid = c.lastrowid
    for pn, page in enumerate(b['pages']):
        insert(db, 'pages', bookid=bookid, pageno=pn,
               caption=page['text'],
               image=page['url'], width=page['width'],
               height=page['height'])
    # create a unique slug
    slugs = db.execute(
        '''
        select slug from shared S, books B
            where S.bookid = B.bookid and B.thrslug = ?
            order by slug
        ''',
        [thrslug]).fetchall()
    if len(slugs) > 0:
        slug = slugs[0] + '{}'.format(len(slugs)+1)
    else:
        slug = thrslug
    # create the shared entry
    c = insert(
        db, 'shared',
        slug=slug, status='draft', owner=teacher, bookid=bookid,
        created=datetime.now(), modified=datetime.now())
    return getBook(db, slug)


@app.route('/log', method='POST')
@with_db
def log(db):
    '''
    Add a record to the log
    '''
    d = request.json
    # get the actual comment
    if d['bookid']:
        row = db.execute('''
            select C.comment from shared S, comments C
                where S.slug = ? and S.sharedid = C.sharedid and
                    C.pageno = ? and C.reading = ?
        ''', (d['bookid'], d['page'], d['reading'])).fetchone()
        if row:
            d['comment'] = row['comment']
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
