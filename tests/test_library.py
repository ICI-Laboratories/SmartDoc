"""Integration tests against a disposable PostgreSQL database named *_test."""
import io
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import UUID, uuid4
from pathlib import Path

TEST_URL = os.environ.get('SARA_TEST_DATABASE_URL')


@unittest.skipUnless(TEST_URL, 'Set SARA_TEST_DATABASE_URL to a disposable *_test database')
class LibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from urllib.parse import urlparse
        if not urlparse(TEST_URL).path.endswith('_test'):
            raise RuntimeError('Refusing to reset a non-test database')
        os.environ['SARA_DATABASE_URL'] = TEST_URL
        cls.temp = tempfile.TemporaryDirectory()
        os.environ['SARA_STORAGE'] = cls.temp.name
        from library_service.api import app
        from library_service.db import make_pool, migrate
        from fastapi.testclient import TestClient
        with make_pool() as pool:
            migrate(pool)
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.pool = app.state.pool
        cls.owner = UUID('123e4567-e89b-12d3-a456-426614174000')
        cls.other = UUID('123e4567-e89b-12d3-a456-426614174001')
        cls.headers = {'X-SmartDoc-Subject': str(cls.owner)}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None,None,None)
        cls.temp.cleanup()

    def setUp(self):
        with self.pool.connection() as conn:
            conn.execute('TRUNCATE documents,usage_events,anonymous_sessions,usage_preferences CASCADE')

    def pdf(self, text='Laboratorio: espectroscopia y calibracion de sensores.', pages=1):
        import pymupdf
        with pymupdf.open() as pdf:
            for _ in range(pages):
                page = pdf.new_page()
                page.insert_text((50,60),text)
            return pdf.tobytes()

    def upload(self, data=None, headers=None, name='paper.pdf'):
        return self.client.post('/api/documents',headers=headers or self.headers,
            files={'file':(name, data if data is not None else self.pdf(), 'application/pdf')})

    def process(self, doc):
        from library_service.worker import claim, extract, finish
        from library_service.db import original
        job=claim(self.pool)
        pages,chunks=extract(original(self.owner,doc['id']))
        finish(self.pool,job,{'pages':pages,'chunks':chunks})
        return job

    def test_upload_available_before_worker_and_authorized_ranges(self):
        data=self.pdf()
        response=self.upload(data)
        self.assertEqual(response.status_code,202,response.text)
        doc=response.json()['document']
        self.assertEqual(doc['status'],'queued')
        self.assertEqual(self.client.get(f"/api/documents/{doc['id']}/file",headers=self.headers).content,data)
        ranged=self.client.get(f"/api/documents/{doc['id']}/file",headers={**self.headers,'Range':'bytes=0-4'})
        self.assertEqual(ranged.status_code,206)
        self.assertEqual(ranged.content,b'%PDF-')
        foreign={'X-SmartDoc-Subject':str(self.other)}
        for suffix in ('','/file'):
            self.assertEqual(self.client.get(f"/api/documents/{doc['id']}{suffix}",headers=foreign).status_code,404)
        self.assertEqual(self.client.get('/api/documents',headers=foreign).json()['items'],[])
        self.assertEqual(self.client.post(f"/api/documents/{doc['id']}/retry",headers=foreign).status_code,404)
        self.assertEqual(self.client.get('/api/search',params={'q':'test','doc_id':doc['id']},headers=foreign).status_code,404)
        self.assertEqual(self.client.post('/api/chat',json={'question':'test','document_id':doc['id']},headers=foreign).status_code,404)

    def test_deduplication_race_is_owner_scoped(self):
        data=self.pdf()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(lambda _:self.upload(data).json(),range(2)))
        self.assertEqual(len({r['document']['id'] for r in results}),1)
        self.assertEqual(sorted(r['duplicate'] for r in results),[False,True])
        other=self.upload(data,{'X-SmartDoc-Subject':str(self.other)}).json()
        self.assertNotEqual(other['document']['id'],results[0]['document']['id'])
        self.assertFalse(other['duplicate'])

    def test_validation_and_no_browser_chosen_subject(self):
        self.assertEqual(self.client.get('/api/documents').status_code,401)
        self.assertEqual(self.client.get('/api/documents',headers={'X-SmartDoc-Subject':'../escape'}).status_code,401)
        self.assertEqual(self.upload(b'not a pdf').status_code,415)
        self.assertEqual(self.upload(b'').status_code,400)
        self.assertEqual(self.upload(name='file.exe').status_code,415)
        with patch.dict(os.environ,{'SARA_MAX_PDF_BYTES':'10'}):
            self.assertEqual(self.upload(self.pdf()).status_code,413)

    def test_lexical_search_and_pagination(self):
        first=self.upload().json()['document']
        self.process(first)
        self.upload(self.pdf('Otro documento independiente'),name='second.pdf')
        rows=self.client.get('/api/documents?limit=1',headers=self.headers).json()
        rest=self.client.get('/api/documents',params={**rows['next'],'limit':1},headers=self.headers).json()
        self.assertNotEqual(rows['items'][0]['id'],rest['items'][0]['id'])
        self.assertEqual(self.client.get('/api/documents?q=second',headers=self.headers).json()['items'][0]['name'],'second.pdf')
        matches=self.client.get('/api/search?q=espectroscopia',headers=self.headers).json()
        self.assertEqual(matches['items'][0]['page'],1)
        self.assertEqual(matches['items'][0]['id'],first['id'])
        foreign=self.client.get('/api/search?q=espectroscopia',headers={'X-SmartDoc-Subject':str(self.other)}).json()
        self.assertEqual(foreign['items'],[])

    def test_expired_lease_recovered_and_stale_completion_rejected(self):
        from library_service.worker import claim, finish
        self.upload()
        old=claim(self.pool)
        self.assertIsNone(claim(self.pool))
        with self.pool.connection() as conn:
            conn.execute("UPDATE jobs SET lease_until=now()-interval '1 second'")
        new=claim(self.pool)
        self.assertEqual(new['attempts'],2)
        self.assertFalse(finish(self.pool,old,{'pages':1,'chunks':[{'page':1,'content':'stale'}]}))
        self.assertTrue(finish(self.pool,new,{'pages':1,'chunks':[{'page':1,'content':'current'}]}))
        with self.pool.connection() as conn:
            self.assertEqual(conn.execute('SELECT content FROM chunks').fetchone()['content'],'current')

    def test_failed_final_attempt_can_be_retried(self):
        from library_service.worker import claim, finish
        doc=self.upload().json()['document']
        for attempt in range(3):
            job=claim(self.pool)
            finish(self.pool,job,{'error':'OCR no disponible'})
            with self.pool.connection() as conn:
                conn.execute("UPDATE jobs SET available_at=now()-interval '1 second'")
        self.assertEqual(self.client.get(f"/api/documents/{doc['id']}",headers=self.headers).json()['status'],'failed')
        self.assertEqual(self.client.post(f"/api/documents/{doc['id']}/retry",headers=self.headers).status_code,202)
        self.assertEqual(claim(self.pool)['attempts'],1)

    def test_final_worker_crash_becomes_failed(self):
        from library_service.worker import claim
        self.upload()
        claim(self.pool)
        with self.pool.connection() as conn:
            conn.execute("UPDATE jobs SET attempts=3,lease_until=now()-interval '1 second'")
        self.assertIsNone(claim(self.pool))
        with self.pool.connection() as conn:
            self.assertEqual(conn.execute('SELECT status FROM documents').fetchone()['status'],'failed')

    def test_hybrid_model_identity_and_offline_fallback(self):
        from library_service.search import retrieve
        from library_service.worker import claim, finish
        self.upload()
        vector=[1.0]+[0.0]*1023
        finish(self.pool,claim(self.pool),{'pages':1,'model':'model-A','chunks':[{'page':1,'content':'espectroscopia','embedding':vector}]})
        with patch.dict(os.environ,{'SARA_EMBEDDING_MODEL':'model-B'}),patch('library_service.search.embed',return_value=[vector]):
            self.assertEqual(retrieve(self.pool,self.owner,'unrelated',semantic=True)['items'],[])
        with patch.dict(os.environ,{'SARA_EMBEDDING_MODEL':'model-A'}),patch('library_service.search.embed',return_value=[vector]):
            self.assertEqual(len(retrieve(self.pool,self.owner,'unrelated',semantic=True)['items']),1)
        with patch.dict(os.environ,{'SARA_EMBEDDING_MODEL':'model-A'}),patch('library_service.search.embed',side_effect=ValueError):
            result=retrieve(self.pool,self.owner,'espectroscopia',semantic=True)
            self.assertEqual(result['mode'],'text_fallback')
            self.assertTrue(result['items'])

    def test_mixed_pdf_ocr_is_per_page_and_keeps_page_numbers(self):
        import pymupdf
        from library_service.worker import extract
        data=self.pdf(pages=2)
        with pymupdf.open(stream=data,filetype='pdf') as pdf:
            pix=pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,20,20),False)
            pix.clear_with(255)
            pdf[1].insert_image(pymupdf.Rect(50,100,200,250),pixmap=pix)
            path=os.path.join(self.temp.name,'mixed.pdf');pdf.save(path)
        calls=[]
        def fake_ocr(page,**kwargs):
            calls.append((page.number,kwargs['full']))
            return page.get_textpage()
        with patch.object(pymupdf.Page,'get_textpage_ocr',fake_ocr):
            count,chunks=extract(path)
        self.assertEqual(count,2)
        self.assertEqual(calls,[(1,False)])
        self.assertEqual({c['page'] for c in chunks},{1,2})

    def test_summary_is_queued_and_persisted_without_placeholder(self):
        import json
        from library_service.worker import claim, process_child, finish
        from library_service.db import original
        doc=self.upload().json()['document']
        self.process(doc)
        with patch.dict(os.environ,{'SARA_LLM_URL':'http://model.test/chat','SARA_LLM_MODEL':'test'}):
            self.assertEqual(self.client.post(f"/api/documents/{doc['id']}/summary",headers=self.headers).status_code,202)
        job=claim(self.pool)
        self.assertTrue(job['summary_requested'])
        output=os.path.join(self.temp.name,'summary-result.json')
        with patch('library_service.enrichment.summarize',return_value=('Síntesis con fuente página 1','Instrumentación')):
            process_child(original(self.owner,doc['id']),output,True)
        finish(self.pool,job,json.loads(Path(output).read_text()))
        detail=self.client.get(f"/api/documents/{doc['id']}",headers=self.headers).json()
        self.assertEqual(detail['category'],'Instrumentación')
        self.assertEqual(detail['summary'],'Síntesis con fuente página 1')

    def test_multidocument_chat_scope_and_stream(self):
        from contextlib import contextmanager
        first=self.upload().json()['document'];self.process(first)
        second=self.upload(self.pdf('Espectroscopia aplicada a otro sensor')).json()['document'];self.process(second)
        foreign=self.upload(self.pdf(),headers={'X-SmartDoc-Subject':str(self.other)}).json()['document']
        blocked=self.client.post('/api/chat',headers=self.headers,json={'question':'espectroscopia','document_ids':[first['id'],foreign['id']]})
        self.assertEqual(blocked.status_code,404)
        class FakeStream:
            def raise_for_status(self): pass
            def iter_lines(self):
                return iter(['data: {"choices":[{"delta":{"content":"Respuesta [Fuente 1]"}}]}','data: [DONE]'])
        @contextmanager
        def fake_stream(*args,**kwargs): yield FakeStream()
        with patch.dict(os.environ,{'SARA_LLM_URL':'http://model.test/chat','SARA_LLM_MODEL':'test'}),patch('library_service.api.httpx.stream',fake_stream):
            result=self.client.post('/api/chat',headers=self.headers,json={'question':'espectroscopia','document_ids':[first['id'],second['id']]})
        self.assertEqual(result.status_code,200)
        self.assertIn(first['id'],result.text);self.assertIn(second['id'],result.text)
        self.assertIn('Respuesta [Fuente 1]',result.text)
        self.assertIn('"done":true',result.text)

    def test_similarity_uses_matching_models_and_rejects_foreign_scope(self):
        from library_service.worker import claim, finish
        ids=[]
        for name in ('A','B'):
            doc=self.upload(self.pdf(name)).json()['document'];ids.append(doc['id'])
            finish(self.pool,claim(self.pool),{'pages':1,'model':'test-model','chunks':[{'page':1,'content':name,'embedding':[1.0]+[0.0]*1023}]})
        with patch.dict(os.environ,{'SARA_EMBEDDING_MODEL':'test-model'}):
            result=self.client.post('/api/similarity',headers=self.headers,json={'document_ids':ids})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(len(result.json()['pairs']),4)
        self.assertTrue(all(pair['similarity']==1.0 for pair in result.json()['pairs']))
        self.assertEqual(self.client.post('/api/similarity',headers={'X-SmartDoc-Subject':str(self.other)},json={'document_ids':ids}).status_code,404)

    def test_manifest_import_dry_run_hash_and_idempotence(self):
        import csv,hashlib
        from library_service.import_legacy import inspect_manifest,import_rows
        root=Path(self.temp.name)/'legacy';folder=root/str(self.owner);folder.mkdir(parents=True,exist_ok=True)
        source=folder/'original.pdf';data=self.pdf();source.write_bytes(data)
        manifest=Path(self.temp.name)/'manifest.csv'
        with manifest.open('w',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['source','subject','sha256']);writer.writerow([source,self.owner,hashlib.sha256(data).hexdigest()])
        rows=inspect_manifest(manifest,root)
        self.assertEqual(self.client.get('/api/documents',headers=self.headers).json()['stats']['total'],0)
        report=import_rows(self.pool,rows)
        self.assertEqual(report[0]['result'],'copied')
        self.assertEqual(import_rows(self.pool,rows)[0]['result'],'already_imported')
        self.assertEqual(source.read_bytes(),data)
        source.write_bytes(data+b'changed')
        with self.assertRaises(ValueError): inspect_manifest(manifest,root)

    def test_summary_reuses_cached_text_and_reports_model_failure(self):
        import json
        from library_service.worker import process_child
        cached=Path(self.temp.name)/'cached.json'
        cached.write_text(json.dumps({'pages':1,'chunks':[{'page':1,'content':'Texto ya extraído'}], 'model':None,'embedding_error':None}))
        output=Path(self.temp.name)/'cached-result.json'
        with patch('library_service.worker.extract',side_effect=AssertionError('Must not re-extract')),patch('library_service.enrichment.summarize',side_effect=ValueError('offline')):
            process_child('unused.pdf',str(output),True,str(cached))
        result=json.loads(output.read_text())
        self.assertIn('summary_error',result)
        self.assertNotIn('summary',result)
        self.assertEqual(result['chunks'][0]['content'],'Texto ya extraído')

    def anonymous(self):
        response=self.client.post('/internal/anonymous/session',json={'create':True})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_anonymous_identity_is_random_persistent_and_not_browser_chosen(self):
        import hashlib
        session=self.anonymous()
        another=self.anonymous()
        self.assertNotEqual(session['subject'],another['subject'])
        self.assertNotEqual(session['token'],another['token'])
        self.assertGreaterEqual(len(session['token']),32)
        resolved=self.client.post('/internal/anonymous/session',json={'token':session['token'],'create':False})
        self.assertEqual(resolved.json()['subject'],session['subject'])
        self.assertIsNone(resolved.json()['token'])
        self.assertEqual(self.client.post('/internal/anonymous/session',json={'token':session['subject'],'create':False}).status_code,401)
        with self.pool.connection() as conn:
            row=conn.execute('SELECT token_hash FROM anonymous_sessions WHERE subject=%s',(session['subject'],)).fetchone()
            self.assertEqual(row['token_hash'],hashlib.sha256(session['token'].encode()).hexdigest())
            conn.execute("UPDATE anonymous_sessions SET expires_at=now()-interval '1 second' WHERE subject=%s",(session['subject'],))
        self.assertEqual(self.client.post('/internal/anonymous/session',json={'token':session['token'],'create':False}).status_code,401)

    def test_analytics_schema_deduplication_and_opt_out(self):
        session=self.anonymous(); headers={'X-SmartDoc-Subject':session['subject']}
        event={'id':str(uuid4()),'event':'answer_feedback','value':'positive'}
        for _ in range(2):
            self.assertEqual(self.client.post('/api/events',headers=headers,json={'events':[event]}).status_code,202)
        with self.pool.connection() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM usage_events').fetchone()['n'],1)
        bad={**event,'question':'private question'}
        self.assertEqual(self.client.post('/api/events',headers=headers,json={'events':[bad]}).status_code,422)
        self.assertEqual(self.client.post('/api/events',headers=headers,json={'events':[{**event,'value':'private text'}]}).status_code,422)
        self.assertEqual(self.client.post('/api/events',headers=headers,json={'events':[{**event,'event':'sql_or_custom_event'}]}).status_code,422)
        self.client.post('/api/analytics/preferences',headers=headers,json={'enabled':False})
        self.assertFalse(self.client.get('/api/analytics/preferences',headers=headers).json()['enabled'])
        from library_service.analytics import record_events
        record_events(self.pool,[dict(id=uuid4(),visitor=session['subject'],event='processing_completed',source='worker')])
        self.client.post('/api/events',headers=headers,json={'events':[{**event,'id':str(uuid4())}]})
        with self.pool.connection() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM usage_events').fetchone()['n'],1)

    def test_analytics_does_not_persist_search_contents_and_retention(self):
        from library_service.api import app
        from library_service.analytics import purge
        session=self.anonymous();headers={'X-SmartDoc-Subject':session['subject']}
        self.client.get('/api/search?q=PRIVATE_DOCUMENT_QUESTION',headers=headers)
        self.client.portal.call(app.state.events.queue.join)
        with self.pool.connection() as conn:
            row=conn.execute("SELECT * FROM usage_events WHERE event='search'").fetchone()
            self.assertIsNotNone(row)
            self.assertNotIn('PRIVATE_DOCUMENT_QUESTION',str(row))
            self.assertIsNone(row['value']);self.assertEqual(row['status'],200)
            conn.execute("UPDATE usage_events SET occurred_at=now()-interval '91 days'")
        purge(self.pool)
        with self.pool.connection() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM usage_events').fetchone()['n'],0)

    def test_browser_telemetry_rate_limit_and_global_disable(self):
        from library_service.analytics import record_events
        session=self.anonymous();headers={'X-SmartDoc-Subject':session['subject']}
        def batch(): return {'events':[{'id':str(uuid4()),'event':'page_view'} for _ in range(20)]}
        for _ in range(6):
            self.assertEqual(self.client.post('/api/events',headers=headers,json=batch()).status_code,202)
        self.assertEqual(self.client.post('/api/events',headers=headers,json=batch()).status_code,429)
        with patch.dict(os.environ,{'SARA_ANALYTICS_ENABLED':'false'}):
            self.assertFalse(self.client.get('/api/analytics/preferences',headers=headers).json()['available'])
            self.assertFalse(self.client.post('/api/events',headers=headers,json=batch()).json()['accepted'])
            record_events(self.pool,[dict(id=uuid4(),visitor=session['subject'],event='processing_completed',source='worker')])
        with self.pool.connection() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM usage_events').fetchone()['n'],120)

    def test_temporary_expiry_removes_files_chunks_jobs_but_preserves_accounts(self):
        from library_service.anonymous import purge_expired
        from library_service.db import original
        guest=self.anonymous()
        headers={'X-SmartDoc-Subject':guest['subject']}
        doc=self.upload(headers=headers).json()['document']
        permanent=self.upload().json()['document']
        path=original(UUID(guest['subject']),UUID(doc['id']))
        self.assertTrue(path.exists())
        self.assertEqual(guest['max_age'],86400)
        with self.pool.connection() as conn:
            conn.execute("UPDATE anonymous_sessions SET expires_at=now()-interval '1 second' WHERE subject=%s",(guest['subject'],))
        with patch.dict(os.environ,{'SARA_ANALYTICS_ENABLED':'false'}):
            purge_expired(self.pool)
        self.assertFalse(path.exists())
        self.assertEqual(self.client.get('/api/documents',headers=headers).status_code,401)
        self.assertEqual(self.upload(headers=headers).status_code,401)
        self.assertEqual(self.client.get('/api/documents/'+permanent['id'],headers=self.headers).status_code,200)
        with self.pool.connection() as conn:
            self.assertIsNone(conn.execute('SELECT 1 FROM documents WHERE id=%s',(doc['id'],)).fetchone())
            self.assertIsNone(conn.execute('SELECT 1 FROM jobs WHERE document_id=%s',(doc['id'],)).fetchone())
        purge_expired(self.pool)  # Idempotent cleanup.

    def test_end_temporary_session_and_preserve_legacy_anonymous_files(self):
        from library_service.db import original
        guest=self.anonymous(); headers={'X-SmartDoc-Subject':guest['subject']}
        doc=self.upload(headers=headers).json()['document']
        legacy=self.anonymous(); old=self.upload(headers={'X-SmartDoc-Subject':legacy['subject']}).json()['document']
        with self.pool.connection() as conn:
            conn.execute('UPDATE anonymous_sessions SET ephemeral=false WHERE subject=%s',(legacy['subject'],))
        for token in [guest['token'],legacy['token']]:
            self.assertEqual(self.client.post('/internal/anonymous/end',json={'token':token}).status_code,200)
        self.assertFalse(original(UUID(guest['subject']),UUID(doc['id'])).exists())
        self.assertTrue(original(UUID(legacy['subject']),UUID(old['id'])).exists())

    def test_account_analytics_preferences_without_anonymous_session(self):
        event={'id':str(uuid4()),'event':'library_view'}
        self.assertTrue(self.client.get('/api/analytics/preferences',headers=self.headers).json()['enabled'])
        self.client.post('/api/events',headers=self.headers,json={'events':[event]})
        self.client.post('/api/analytics/preferences',headers=self.headers,json={'enabled':False})
        self.client.post('/api/events',headers=self.headers,json={'events':[{**event,'id':str(uuid4())}]})
        with self.pool.connection() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM usage_events').fetchone()['n'],1)


if __name__=='__main__':
    unittest.main()
