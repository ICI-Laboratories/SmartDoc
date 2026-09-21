import os
from pathlib import Path
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def make_pool():
    return ConnectionPool(os.environ['SARA_DATABASE_URL'], min_size=1, max_size=8,
                          timeout=5, open=False, kwargs={'row_factory': dict_row})


def migrate(pool):
    with pool.connection() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(738241902)')
        conn.execute(Path(__file__).with_name('schema.sql').read_text())


def storage():
    return Path(os.environ.get('SARA_STORAGE', './sara_data')).resolve()


def original(subject, document_id):
    # Both arguments must already be UUIDs obtained from trusted database rows.
    return storage() / str(subject) / f'{document_id}.pdf'
