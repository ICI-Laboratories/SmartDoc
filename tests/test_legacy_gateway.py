"""Legacy SmartDoc shares the same inference gateway contract as DocReader."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pymupdf
import requests

from document_processor.core_pdf import convert_pdf_to_markdown, embedding_passages
from library_service.search import embedding_identity
from llm_service.logic import analysis, core


class LegacyGatewayTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            'LLM_GATEWAY_BASE_URL': 'http://gateway.test:8000/v1',
            'LLM_GATEWAY_API_KEY': 'smartdoc-test-key',
            'SARA_EMBEDDING_MODEL': 'bge-m3',
            'SARA_EMBEDDING_REVISION': 'test-v1',
            'SARA_OCR_MODEL': '',
            'SMARTREVIEW_LM_URL': 'http://old-provider:11434/v1/chat/completions',
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def response(self, content, finish='stop'):
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'choices': [{'finish_reason': finish, 'message': {'content': content}}]}).encode()
        return response

    def test_structured_chat_uses_gateway_auth_and_openai_schema(self):
        schema = {'type': 'object', 'properties': {'answer': {'type': 'string'}}}
        with patch.object(core.requests, 'post', return_value=self.response('{"answer":"ok"}')) as post:
            self.assertEqual(core.call_llm('hello', schema), {'answer': 'ok'})
        self.assertEqual(post.call_args.args[0], 'http://gateway.test:8000/v1/chat/completions')
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer smartdoc-test-key')
        self.assertFalse(kwargs['allow_redirects'])
        self.assertNotIn('format', kwargs['json'])
        self.assertEqual(kwargs['json']['response_format']['json_schema']['schema'], schema)

    def test_missing_key_never_contacts_another_engine(self):
        os.environ['LLM_GATEWAY_API_KEY'] = ''
        with patch.object(core.requests, 'post') as post:
            self.assertIn('error', core.call_llm('hello'))
            post.assert_not_called()

    def test_truncated_response_is_not_accepted(self):
        with patch.object(core.requests, 'post', return_value=self.response('partial', 'length')):
            self.assertIn('error', core.call_llm('hello'))

    def test_vector_model_identity_prevents_mixing_indexes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'doc.npz'
            kwargs = {'chunks': np.array(['texto']), 'embeddings': np.ones((1, 1024))}
            np.savez(path, **kwargs)
            with self.assertRaises(ValueError):
                analysis._load_npz(path)
            np.savez(path, **kwargs, embedding_identity=np.array('gateway:bge-m3@old'))
            with self.assertRaises(ValueError):
                analysis._load_npz(path)
            np.savez(path, **kwargs, embedding_identity=np.array(embedding_identity()))
            self.assertEqual(analysis._load_npz(path)[1].shape, (1, 1024))

    def test_hybrid_query_uses_gateway_embedding_and_matching_index(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'doc.md'
            np.savez(path.with_suffix('.npz'), chunks=np.array(['laboratorio química']),
                     embeddings=np.ones((1, 1024)), embedding_identity=np.array(embedding_identity()))
            with patch.object(analysis, 'embed', return_value=[[1.0] * 1024]) as embed:
                result = analysis.hybrid_search_in_docs([str(path)], 'química')
            embed.assert_called_once_with(['química'], is_query=True)
            self.assertEqual(result[0]['document'], 'doc')

    def test_opt_in_ocr_preserves_native_and_scanned_pages(self):
        os.environ['SARA_OCR_MODEL'] = 'ocr'
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), 'Native page')
            pdf.new_page()
            payload = pdf.tobytes()
        with patch('document_processor.core_pdf.extract_page', return_value='Scanned page') as ocr:
            result = convert_pdf_to_markdown(payload)
        self.assertEqual(ocr.call_count, 1)
        self.assertIn('--- Página 1 ---\n\nNative page', result)
        self.assertIn('--- Página 2 ---\n\nScanned page', result)

    def test_long_embedding_paragraph_is_bounded_without_losing_characters(self):
        paragraph = ('Calibración αβ, instrumentos y medidas. ' * 300).strip()
        self.assertGreater(len(paragraph), 10000)
        chunks = list(embedding_passages(paragraph))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(0 < len(chunk) <= 1800 for chunk in chunks))
        reconstructed = chunks[0]
        for previous, current in zip(chunks, chunks[1:]):
            self.assertEqual(previous[-180:], current[:180])
            reconstructed += current[180:]
        self.assertEqual(reconstructed, paragraph)

    def test_embedding_paragraph_selection_keeps_short_tail_of_long_paragraph(self):
        first = 'a' * 1801
        second = 'A complete second paragraph with enough characters.'
        self.assertEqual(list(embedding_passages(first + '\n\nshort header\n\n' + second)),
                         ['a' * 1800, 'a' * 181, second])

    def test_failed_ocr_does_not_return_partial_document(self):
        os.environ['SARA_OCR_MODEL'] = 'ocr'
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), 'Native page')
            pdf.new_page()
            payload = pdf.tobytes()
        with patch('document_processor.core_pdf.extract_page', side_effect=ValueError('Unavailable')):
            with self.assertRaises(ValueError):
                convert_pdf_to_markdown(payload)


if __name__ == '__main__':
    unittest.main()
