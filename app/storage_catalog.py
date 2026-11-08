"""A bounded, private index for browsing workspace file ownership without rescanning."""
import json
from pathlib import Path
import secrets
import sqlite3
import threading

import storage_inventory

_PAGE_LIMIT = 100
_IDLE_SECONDS = 20 * 60
_MAX_PAGES = 65536  # 256 MiB at 4 KiB per page; large catalogues fall back to explicit scans.


def _searchable(row):
    return '\n'.join([row['path'], *row.get('owners',()), row.get('type',''), row['category']]).casefold()


class Catalog:
    """Keep one immutable catalogue per application process; old views expire cleanly."""
    def __init__(self):
        self.scan_lock = threading.Lock()
        self.lock = threading.Lock()
        self.active = None
        self.expiry = None

    def _drop_locked(self):
        if self.expiry:
            self.expiry.cancel()
            self.expiry = None
        if self.active and self.active['db'] is not None:
            self.active['db'].close()
        self.active = None

    def _expire(self, token):
        with self.lock:
            if self.active and self.active['token'] == token:
                self._drop_locked()

    def scan(self, work, state):
        if not self.scan_lock.acquire(blocking=False):
            raise ValueError('Disk inspection is already running. Try again when it finishes.')
        db = None
        try:
            # An empty SQLite filename opens a private temporary database. SQLite
            # removes it when closed; startup does not create a cache in the project.
            try:
                db = sqlite3.connect('', check_same_thread=False)
                db.execute('PRAGMA page_size=4096')
                db.execute(f'PRAGMA max_page_count={_MAX_PAGES}')
                db.execute('PRAGMA journal_mode=OFF')
                db.execute('PRAGMA cache_size=-2048')
                db.execute('CREATE TABLE files (search_text TEXT, data TEXT)')
            except sqlite3.Error:
                if db is not None:
                    db.close()
                db = None
            count = 0
            def add(row):
                nonlocal count, db
                count += 1
                if db is not None:
                    try:
                        db.execute('INSERT INTO files VALUES (?,?)', (_searchable(row),json.dumps(row,ensure_ascii=True)))
                    except (sqlite3.Error, UnicodeError):
                        # Disk/capacity constraints must not hide the remaining files.
                        db.close()
                        db = None

            summary = storage_inventory.inventory(work,state,index_sink=add)
            if db is not None:
                try:
                    db.commit()
                except sqlite3.Error:
                    db.close()
                    db = None
            token = secrets.token_hex(16)
            with self.lock:
                self._drop_locked()
                self.active = dict(token=token,root=Path(work).resolve(),db=db,count=count)
                db = None
                self.expiry = threading.Timer(_IDLE_SECONDS,self._expire,args=(token,))
                self.expiry.daemon = True
                self.expiry.start()
                live = self.active['db'] is None
            return {**summary, 'catalog_token':token,'catalog_files':count,'catalog_live_pages':live}
        finally:
            if db is not None:
                db.close()
            self.scan_lock.release()

    def page(self, work, state_factory, token, text='', offset=0, limit=50):
        if not isinstance(text,str) or len(text)>256 or type(offset) is not int or not 0<=offset<=2**63-1 or type(limit) is not int or not 1<=limit<=_PAGE_LIMIT:
            raise ValueError('Invalid storage search or page.')
        query=text.strip().casefold()
        with self.lock:
            active=self.active
            if not active or token!=active['token'] or Path(work).resolve()!=active['root']:
                raise ValueError('This folder inspection has expired. Choose Scan again to refresh it.')
            db=active['db']
            if db is not None:
                where,params=(' WHERE instr(search_text,?)>0',(query,)) if query else ('',())
                total=db.execute('SELECT count(*) FROM files'+where,params).fetchone()[0] if query else active['count']
                records=db.execute('SELECT data FROM files'+where+' ORDER BY rowid LIMIT ? OFFSET ?',(*params,limit,offset))
                return dict(files=[json.loads(row[0]) for row in records],total=total,offset=offset,limit=limit)
        # Low temporary space or index limit. Scan once per user-requested page
        # keeping only that page's matches; use the same protection/owner rules.
        rows=[]
        total=0
        def collect(row):
            nonlocal total
            if not query or query in _searchable(row):
                if offset<=total<offset+limit:
                    rows.append(row)
                total+=1
        storage_inventory.inventory(work,state_factory(),index_sink=collect)
        return dict(files=rows,total=total,offset=offset,limit=limit)

catalog=Catalog()
