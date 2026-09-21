"""Explicit, copy-only migration. CSV: source,subject,sha256. Dry-run by default.

Only canonical UUID namespaces are accepted. Anonymous spaces require the separate
reviewed reconciliation procedure; this tool never guesses their owner.
"""
import argparse
import csv
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from uuid import UUID, uuid4
from .db import make_pool, original


def inspect_manifest(manifest, source_root):
    root = Path(source_root).resolve(strict=True)
    rows = []
    with open(manifest, newline='', encoding='utf-8') as stream:
        for line, row in enumerate(csv.DictReader(stream), 2):
            subject = UUID(row['subject'])
            if str(subject) != row['subject']:
                raise ValueError(f'Fila {line}: UUID no canónico')
            source = Path(row['source']).resolve(strict=True)
            if not source.is_relative_to(root / str(subject)):
                raise ValueError(f'Fila {line}: archivo fuera del namespace UUID declarado')
            if source.suffix.lower() != '.pdf' or not source.is_file():
                raise ValueError(f'Fila {line}: se requiere un PDF')
            with source.open('rb') as file:
                if file.read(5) != b'%PDF-':
                    raise ValueError(f'Fila {line}: contenido no PDF')
                file.seek(0)
                digest = hashlib.file_digest(file, 'sha256').hexdigest()
            if digest != row['sha256']:
                raise ValueError(f'Fila {line}: hash diferente al manifiesto')
            rows.append({'source': source, 'subject': subject, 'sha256': digest, 'size': source.stat().st_size})
    return rows


def import_rows(pool, rows):
    report = []
    for row in rows:
        with pool.connection() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (str(row['subject']) + row['sha256'],))
            existing = conn.execute('SELECT id FROM documents WHERE subject=%s AND sha256=%s', (row['subject'],row['sha256'])).fetchone()
            if existing:
                report.append({'source': str(row['source']), 'id': str(existing['id']), 'result': 'already_imported'})
                continue
            doc_id = uuid4()
            target = original(row['subject'], doc_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output, row['source'].open('rb') as source:
                    temp_path = Path(output.name)
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fsync(output.fileno())
                with temp_path.open('rb') as copied:
                    if hashlib.file_digest(copied,'sha256').hexdigest()!=row['sha256']:
                        raise ValueError('El origen cambió durante la copia; no se registró el documento')
                temp_path.replace(target)
                conn.execute('INSERT INTO documents(id,subject,name,sha256,size) VALUES (%s,%s,%s,%s,%s)',
                    (doc_id,row['subject'],row['source'].name,row['sha256'],row['size']))
                conn.execute('INSERT INTO jobs(document_id) VALUES (%s)', (doc_id,))
            finally:
                if temp_path:
                    temp_path.unlink(missing_ok=True)
            report.append({'source': str(row['source']), 'id': str(doc_id), 'result': 'copied'})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    rows = inspect_manifest(args.manifest,args.source_root)
    if not args.apply:
        print(json.dumps({'mode':'dry-run','documents':len(rows),'bytes':sum(r['size'] for r in rows)},indent=2))
        return
    with make_pool() as pool:
        pool.wait()
        report=import_rows(pool,rows)
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
