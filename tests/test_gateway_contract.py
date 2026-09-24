"""Offline contracts for the app-to-gateway migration; no model or DB needed."""
import asyncio
import base64
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pymupdf

from library_service import gateway, search, worker
from library_service.enrichment import summarize
from library_service.ocr import extract_page


ENV = {
    'LLM_GATEWAY_BASE_URL': 'http://gateway.test:8009/v1/',
    'LLM_GATEWAY_API_KEY': 'test-key',
    'SARA_EMBEDDING_MODEL': 'bge-m3',
    'SARA_EMBEDDING_REVISION': 'test-v1',
    'SARA_LLM_MODEL': 'sara-main',
    'SARA_OCR_MODEL': '',
}


def response(payload, status=200):
    return httpx.Response(status, json=payload, request=httpx.Request('POST', 'http://gateway.test/v1'))


class GatewayContractTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        # CPU verification is covered separately; these tests isolate HTTP contracts.
        verifier = patch('library_service.ocr.verify_and_preserve', side_effect=lambda text, *args: text)
        cpu = patch('library_service.ocr.tesseract_lines', return_value=[])
        verifier.start()
        cpu.start()
        self.addCleanup(verifier.stop)
        self.addCleanup(cpu.stop)

    def test_gateway_normalizes_base_and_requires_bearer_key(self):
        self.assertEqual(gateway.gateway_url('/embeddings'), 'http://gateway.test:8009/v1/embeddings')
        self.assertEqual(gateway.gateway_headers(), {'Authorization': 'Bearer test-key'})
        with patch.dict(os.environ, {'LLM_GATEWAY_BASE_URL': 'https://gateway.test'}):
            self.assertEqual(gateway.gateway_url('chat/completions'), 'https://gateway.test/v1/chat/completions')
        with patch.dict(os.environ, {'LLM_GATEWAY_API_KEY': ''}), patch('httpx.post') as post:
            self.assertFalse(gateway.gateway_ready())
            with self.assertRaisesRegex(ValueError, 'LLM_GATEWAY_API_KEY'):
                search.embed(['hello'])
            post.assert_not_called()
        for url in ('http://user:secret@gateway.test/v1', 'file:///models', 'https://gateway.test/v1?key=secret',
                    'http://gateway.test/v1/chat/completions', 'http://gateway.test/api/embed',
                    'http://gateway.test:bad/v1', 'http://gateway.test:99999/v1', 'http://gateway.test/other'):
            with self.subTest(url=url), patch.dict(os.environ, {'LLM_GATEWAY_BASE_URL': url}):
                self.assertFalse(gateway.gateway_ready())

    def test_embeddings_openai_contract_auth_and_index_order(self):
        first, second = [1.0] + [0.0] * 1023, [0.0, 1.0] + [0.0] * 1022
        payload = {'data': [{'index': 1, 'embedding': second}, {'index': 0, 'embedding': first}]}
        with patch('httpx.post', return_value=response(payload)) as post:
            self.assertEqual(search.embed(['first', 'second']), [first, second])
        self.assertEqual(post.call_args.args, ('http://gateway.test:8009/v1/embeddings',))
        self.assertEqual(post.call_args.kwargs['headers'], {'Authorization': 'Bearer test-key'})
        self.assertEqual(post.call_args.kwargs['json'], {
            'model': 'bge-m3', 'input': ['first', 'second'], 'encoding_format': 'float'})
        self.assertEqual(search.embedding_identity(), 'gateway:bge-m3@test-v1')

    def test_embeddings_reject_incomplete_duplicate_nonfinite_and_wrong_dimension(self):
        valid = [1.0] * 1024
        invalid = [
            {'data': []}, {'embeddings': [valid]}, {'data': [{'embedding': valid}]},
            {'data': [{'index': 1, 'embedding': valid}]},
            {'data': [{'index': True, 'embedding': valid}]},
            {'data': [{'index': 0, 'embedding': [1.0] * 768}]},
            {'data': [{'index': 0, 'embedding': [0.0] * 1024}]},
            {'data': [{'index': 0, 'embedding': ['1'] * 1024}]},
            {'data': [{'index': 0, 'embedding': [True] * 1024}]},
        ]
        for payload in invalid:
            with self.subTest(payload=str(payload)[:90]), patch('httpx.post', return_value=response(payload)):
                with self.assertRaises(ValueError):
                    search.embed(['text'])
        for number in (float('nan'), float('inf')):
            mocked = MagicMock()
            mocked.json.return_value = {'data': [{'index': 0, 'embedding': [number] + valid[1:]}]}
            with patch('httpx.post', return_value=mocked), self.assertRaises(ValueError):
                search.embed(['text'])
        payload = {'data': [{'index': 0, 'embedding': valid}, {'index': 0, 'embedding': valid}]}
        with patch('httpx.post', return_value=response(payload)), self.assertRaises(ValueError):
            search.embed(['one', 'two'])

    def test_optional_query_instruction_applies_to_queries_only_and_changes_identity(self):
        instruction = 'Given a web search query, retrieve relevant passages that answer the query'
        payload = {'data': [{'index': 0, 'embedding': [1.0] * 1024}]}
        previous_identity = search.embedding_identity()
        with patch.dict(os.environ, {'SARA_EMBEDDING_QUERY_INSTRUCTION': instruction}), \
                patch('httpx.post', return_value=response(payload)) as post:
            search.embed(['document passage'])
            self.assertEqual(post.call_args.kwargs['json']['input'], ['document passage'])
            search.embed(['¿Qué es la calibración?'], is_query=True)
            self.assertEqual(post.call_args.kwargs['json']['input'], [
                f'Instruct: {instruction}\nQuery: ¿Qué es la calibración?'])
            identity = search.embedding_identity()
            self.assertNotEqual(identity, previous_identity)
            self.assertTrue(identity.startswith(previous_identity + ':query-sha256:'))
            self.assertEqual(identity, search.embedding_identity())
            with patch.dict(os.environ, {'SARA_EMBEDDING_QUERY_INSTRUCTION': instruction + ' accurately'}):
                self.assertNotEqual(identity, search.embedding_identity())
        self.assertEqual(search.embedding_identity(), previous_identity)

    def test_semantic_retrieval_adds_configured_query_instruction_and_matching_identity(self):
        pool = MagicMock()
        pool.connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
        with patch.dict(os.environ, {'SARA_EMBEDDING_QUERY_INSTRUCTION': 'Find relevant passages'}), \
                patch('httpx.post', return_value=response({'data': [{'index': 0, 'embedding': [1.0] * 1024}]})) as post:
            self.assertEqual(search.retrieve(pool, 'owner', 'química', semantic=True)['mode'], 'hybrid')
            self.assertEqual(post.call_args.kwargs['json']['input'], [
                'Instruct: Find relevant passages\nQuery: química'])
            params = pool.connection.return_value.__enter__.return_value.execute.call_args.args[1]
            self.assertEqual(params['model'], search.embedding_identity())

    def test_auxiliary_disabled_no_network_even_with_legacy_urls(self):
        with patch.dict(os.environ, {'SARA_EMBEDDING_MODEL': '', 'SARA_LLM_MODEL': '',
                                    'SARA_OLLAMA_URL': 'http://old.test', 'SARA_LLM_URL': 'http://old.test/chat'}), \
                patch('httpx.post') as post:
            self.assertIsNone(search.embed(['text']))
            self.assertEqual(search.embedding_identity(), '')
            with self.assertRaises(ValueError):
                summarize([{'page': 1, 'content': 'text'}])
            with pymupdf.open() as pdf:
                with self.assertRaises(ValueError):
                    extract_page(pdf.new_page())
            post.assert_not_called()

    def test_missing_embeddings_endpoint_preserves_lexical_search_and_ingestion(self):
        pool = MagicMock()
        pool.connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = [
            {'id': 'document', 'ordinal': 0, 'page': 1, 'content': 'calibración', 'name': 'Manual'}]
        with patch('httpx.post', return_value=response({'detail': 'not available'}, status=404)):
            result = search.retrieve(pool, 'owner', 'calibración', semantic=True)
            self.assertEqual(result['mode'], 'text_fallback')
            self.assertEqual(len(result['items']), 1)
            with tempfile.TemporaryDirectory() as tmp, \
                    patch('library_service.worker.extract', return_value=(1, [{'page': 1, 'content': 'calibración'}])):
                output = Path(tmp) / 'output.json'
                worker.process_child('unused.pdf', output)
                result = json.loads(output.read_text())
                self.assertIn('búsqueda por texto', result['embedding_error'])
                self.assertIsNone(result['model'])
                self.assertNotIn('embedding', result['chunks'][0])
                self.assertNotIn('error', result)

    def test_ingestion_and_query_use_same_gateway_identity(self):
        vector = [1.0] * 1024
        with patch('httpx.post', return_value=response({'data': [{'index': 0, 'embedding': vector}]})):
            with tempfile.TemporaryDirectory() as tmp, \
                    patch('library_service.worker.extract', return_value=(1, [{'page': 1, 'content': 'text'}])):
                output = Path(tmp) / 'result.json'
                worker.process_child('unused.pdf', output)
                self.assertEqual(json.loads(output.read_text())['model'], search.embedding_identity())
            pool = MagicMock()
            pool.connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
            self.assertEqual(search.retrieve(pool, 'owner', 'text', semantic=True)['mode'], 'hybrid')
            params = pool.connection.return_value.__enter__.return_value.execute.call_args.args[1]
            self.assertEqual(params['model'], search.embedding_identity())
        original = search.embedding_identity()
        with patch.dict(os.environ, {'SARA_EMBEDDING_REVISION': 'test-v2'}):
            self.assertNotEqual(original, search.embedding_identity())

    def test_summary_and_stream_chat_use_gateway_auth(self):
        result = {'choices': [{'message': {'content': json.dumps({'summary': 'Resumen', 'category': 'Ciencia'})}}]}
        with patch('httpx.post', return_value=response(result)) as post:
            self.assertIn('Resumen', summarize([{'page': 1, 'content': 'text'}])[0])
            self.assertEqual(post.call_args.args[0], 'http://gateway.test:8009/v1/chat/completions')
            self.assertEqual(post.call_args.kwargs['headers'], {'Authorization': 'Bearer test-key'})

        from library_service import api
        doc_id, owner = uuid4(), uuid4()
        fake_response = MagicMock()
        fake_response.iter_lines.return_value = iter([
            'data: {"choices":[{"delta":{"content":"Respuesta"}}]}', 'data: [DONE]'])

        @contextmanager
        def fake_stream(*args, **kwargs):
            self.assertEqual(args, ('POST', 'http://gateway.test:8009/v1/chat/completions'))
            self.assertEqual(kwargs['headers'], {'Authorization': 'Bearer test-key'})
            yield fake_response

        async def consume(streaming_response):
            return ''.join([part async for part in streaming_response.body_iterator])

        with patch.object(api.app.state, 'pool', MagicMock(), create=True), patch.object(api, 'owned'), \
                patch.object(api.analytics, 'record_events'), patch('httpx.stream', fake_stream), \
                patch.object(api, 'retrieve', return_value={'items': [{'id': doc_id, 'page': 1, 'content': 'text', 'name': 'Doc'}]}):
            stream = api.chat(api.ChatRequest(question='text', document_ids=[doc_id]), owner)
            output = asyncio.run(consume(stream))
        self.assertIn('Respuesta', output)
        self.assertIn('"done":true', output)

    def test_gateway_ocr_scanned_and_native_pages_preserve_order(self):
        payload = {'choices': [{'finish_reason': 'stop', 'message': {'content': 'Texto escaneado'}}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'mixed.pdf'
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((40, 60), 'Texto nativo')
                pdf.new_page(width=3000, height=4000)
                pdf.save(path)
            with patch.dict(os.environ, {'SARA_OCR_MODEL': 'ocr-test'}), \
                    patch('httpx.post', return_value=response(payload)) as post, \
                    patch.object(pymupdf.Page, 'get_textpage_ocr', side_effect=AssertionError('CPU OCR not expected')):
                pages, chunks = worker.extract(path)
            self.assertEqual(pages, 2)
            self.assertEqual([chunk['page'] for chunk in chunks], [1, 2])
            self.assertIn('Texto nativo', chunks[0]['content'])
            self.assertEqual(chunks[1]['content'], 'Texto escaneado')
            post.assert_called_once()
            request = post.call_args.kwargs
            self.assertEqual(request['headers'], {'Authorization': 'Bearer test-key'})
            self.assertEqual(request['json']['model'], 'ocr-test')
            image_url = request['json']['messages'][0]['content'][1]['image_url']['url']
            image = pymupdf.Pixmap(base64.b64decode(image_url.split(',', 1)[1]))
            self.assertLessEqual(max(image.width, image.height), 1600)

    def test_lighton_ocr_sends_only_image_and_caps_rendered_long_side(self):
        payload = {'choices': [{'finish_reason': 'stop', 'message': {'content': 'Recognized page'}}]}
        for configured_side, expected in ((2048, 1540), (1024, 1024)):
            with self.subTest(max_side=configured_side), pymupdf.open() as pdf, patch.dict(os.environ, {
                    'SARA_OCR_MODEL': 'ocr', 'SARA_OCR_PROFILE': 'lightonocr-2',
                    'SARA_OCR_MAX_SIDE': str(configured_side)}), \
                    patch('httpx.post', return_value=response(payload)) as post:
                self.assertEqual(extract_page(pdf.new_page()), 'Recognized page')
                content = post.call_args.kwargs['json']['messages'][0]['content']
                self.assertEqual([part['type'] for part in content], ['image_url'])
                image = pymupdf.Pixmap(base64.b64decode(content[0]['image_url']['url'].split(',', 1)[1]))
                self.assertEqual(max(image.width, image.height), expected)

    def test_unknown_ocr_profile_is_rejected_before_request(self):
        with pymupdf.open() as pdf, patch.dict(os.environ, {
                'SARA_OCR_MODEL': 'ocr', 'SARA_OCR_PROFILE': 'unknown'}), patch('httpx.post') as post:
            with self.assertRaisesRegex(ValueError, 'SARA_OCR_PROFILE'):
                extract_page(pdf.new_page())
            post.assert_not_called()

    def test_gateway_ocr_failure_does_not_drop_a_page_or_fallback_to_chat(self):
        cases = [response({'error': 'not available'}, 404),
                 response({'choices': [{'finish_reason': 'length', 'message': {'content': 'Partial'}}]}),
                 response({'choices': [{'finish_reason': 'stop', 'message': {'content': ''}}]})]
        with tempfile.TemporaryDirectory() as tmp:
            path, output = Path(tmp) / 'scanned.pdf', Path(tmp) / 'result.json'
            with pymupdf.open() as pdf:
                pdf.new_page()
                pdf.save(path)
            for upstream in cases:
                with patch.dict(os.environ, {'SARA_OCR_MODEL': 'ocr-test'}), \
                        patch('httpx.post', return_value=upstream) as post:
                    with self.assertLogs('library_service.worker', level='ERROR'):
                        worker.process_child(path, output)
                    result = json.loads(output.read_text())
                    self.assertIn('página 1', result['error'])
                    self.assertNotIn('chunks', result)
                    self.assertEqual(post.call_args.kwargs['json']['model'], 'ocr-test')

    def test_cpu_ocr_remains_default_for_unprovisioned_gateway(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'scanned.pdf'
            with pymupdf.open() as pdf:
                pdf.new_page()
                pdf.save(path)
            calls = []

            def cpu_ocr(page, **kwargs):
                calls.append(kwargs)
                page.insert_text((40, 60), 'Texto CPU')
                return page.get_textpage()

            with patch.object(pymupdf.Page, 'get_textpage_ocr', cpu_ocr), patch('httpx.post') as post:
                self.assertEqual(worker.extract(path)[1][0]['content'], 'Texto CPU')
                post.assert_not_called()
            self.assertEqual(calls, [{'language': 'spa+eng', 'dpi': 150, 'full': True}])

    def test_image_only_pdf_ocr_is_sequential_and_keeps_every_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'image-only.pdf'
            with pymupdf.open() as source:
                source.new_page().insert_text((40, 60), 'Scanned text')
                image = source[0].get_pixmap()
            with pymupdf.open() as pdf:
                for _ in range(2):
                    page = pdf.new_page()
                    page.insert_image(page.rect, pixmap=image)
                pdf.save(path)
            payloads = [response({'choices': [{'finish_reason': 'stop', 'message': {'content': text}}]})
                        for text in ('Primera página', 'Segunda página')]
            with patch.dict(os.environ, {'SARA_OCR_MODEL': 'ocr-test'}), \
                    patch('httpx.post', side_effect=payloads) as post:
                pages, chunks = worker.extract(path)
            self.assertEqual(pages, 2)
            self.assertEqual(chunks, [{'page': 1, 'content': 'Primera página'}, {'page': 2, 'content': 'Segunda página'}])
            self.assertEqual(post.call_count, 2)

    def test_cpu_blank_pdf_rejected_but_mixed_blank_page_count_preserved(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(pymupdf.Page, 'get_textpage_ocr', lambda page, **kwargs: page.get_textpage()), \
                patch('httpx.post') as post:
            blank, mixed = Path(tmp) / 'blank.pdf', Path(tmp) / 'mixed.pdf'
            with pymupdf.open() as pdf:
                pdf.new_page()
                pdf.save(blank)
                pdf.new_page().insert_text((40, 60), 'Texto nativo')
                pdf.save(mixed)
            with self.assertRaisesRegex(ValueError, 'No se encontró texto legible'):
                worker.extract(blank)
            pages, chunks = worker.extract(mixed)
            self.assertEqual(pages, 2)
            self.assertEqual(chunks, [{'page': 2, 'content': 'Texto nativo'}])
            post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
