"""OCR omissions, independent evidence, and preservation contracts."""
import subprocess
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from library_service import ocr_quality as quality


def line(text, top=400, bottom=425, confidence=98):
    return quality.VerifiedLine(text, top, bottom, tuple((word, confidence) for word in text.split()))


class OCRQualityTests(unittest.TestCase):
    def test_recovers_header_and_footer_without_rewriting_tables_or_math(self):
        body = "Medición de resistencia eléctrica.\n\n<table><tr><td>12,4</td></tr></table>\n$$V=IR$$"
        verified = quality.verify_and_preserve(body, 1000, [
            line("INFORME DE LABORATORIO", 25, 45), line("Medición de resistencia eléctrica."),
            line("Página 1", 955, 975),
        ])
        self.assertTrue(verified.startswith(body))
        self.assertIn("### Texto de márgenes recuperado por verificación OCR", verified)
        self.assertIn("INFORME DE LABORATORIO", verified)
        self.assertIn("Página 1", verified)

    def test_missing_interior_paragraph_is_not_accepted(self):
        with self.assertRaisesRegex(ValueError, "omitido.*revisión"):
            quality.verify_and_preserve("Primera columna incluye procedimiento completo.", 1000, [
                line("Primera columna incluye procedimiento completo."),
                line("Segunda columna explica resultados experimentales inesperados."),
            ])

    def test_keeps_column_reading_order_and_cross_line_paragraph(self):
        text = ("Primera columna: observaciones de resistencia eléctrica.\n\n"
                "Segunda columna: incertidumbre experimental y conclusiones.")
        verified = quality.verify_and_preserve(text, 1000, [
            line("Primera columna observaciones de resistencia"),
            line("Segunda columna incertidumbre experimental"),
            line("eléctrica conclusiones", 450, 475),
        ])
        self.assertEqual(verified, text)

    def test_accent_punctuation_and_numeric_layout_differences_are_not_omissions(self):
        text = "Medición eléctrica con multimetro e incertidumbre.\n|12,4|0,48|\n$$E=mc^2$$"
        self.assertEqual(quality.verify_and_preserve(text, 1000, [
            line("Medición eléctrica con multímetro e incertidumbre"),
            line("12.4 0.48 E=m c²"),
        ]), text)

    def test_preserves_missing_native_text_exactly_once(self):
        text = "El cuerpo completo muestra resultados experimentales."
        native = "Título nativo: edición 2026\nPágina 1"
        result = quality.verify_and_preserve(text, 1000, [line(text)], native)
        self.assertEqual(result.count(native), 1)
        self.assertIn("### Texto nativo del PDF conservado", result)
        self.assertTrue(result.startswith(text))
        self.assertEqual(quality.verify_and_preserve(text + "\n" + native, 1000, [line(text)], native),
                         text + "\n" + native)

    def test_native_signs_symbols_and_order_are_preserved_even_when_word_bags_match(self):
        cases = [
            ("La corriente medida del circuito representa 5 amperios.",
             "La corriente medida del circuito representa -5 amperios."),
            ("El cuerpo completo muestra resultados experimentales. Valor 5.",
             "Δ = −5 Ω; α ≠ β"),
            ("El paciente observa al especialista durante consulta.",
             "El especialista observa al paciente durante consulta."),
        ]
        for recognized, native in cases:
            with self.subTest(native=native):
                result = quality.verify_and_preserve(recognized, 1000, [line(recognized)], native)
                self.assertTrue(result.startswith(recognized))
                self.assertIn(native, result)
                self.assertEqual(result.count("### Texto nativo del PDF conservado"), 1)

    def test_exact_native_text_is_not_appended_twice(self):
        native = "Δ = −5 Ω; α ≠ β"
        text = "Contenido escaneado.\n\n" + native
        self.assertEqual(quality.preserve_native(text, native), text)
        self.assertEqual(quality.preserve_native(text, " \n"), text)
        first = quality.preserve_native("Contenido escaneado.", native)
        self.assertEqual(quality.preserve_native(first, native), first)

    def test_native_evidence_can_preserve_a_missing_mixed_page_interior(self):
        body = "Texto reconocido del bloque escaneado."
        native = "Segundo bloque nativo con datos originales."
        result = quality.verify_and_preserve(body, 1000, [line(body), line(native)], native)
        self.assertIn(native, result)

    def test_blank_or_unreadable_scan_is_not_declared_verified(self):
        for lines in ([], [line("ruido ilegible sin confianza", confidence=35)]):
            with self.subTest(lines=lines), self.assertRaisesRegex(ValueError, "suficiente.*revisión"):
                quality.verify_and_preserve("Un texto inventado plausible.", 1000, lines)
        with self.assertRaisesRegex(ValueError, "suficiente.*revisión"):
            quality.verify_and_preserve("Un texto inventado plausible.", 1000, [], "Encabezado nativo")

    def test_low_confidence_noise_does_not_override_body(self):
        text = "Texto completo verificado en laboratorio."
        self.assertEqual(quality.verify_and_preserve(text, 1000, [
            line(text), line("xxyyzz numeración falsa perdida", confidence=40),
        ]), text)

    def test_degenerate_stop_response_is_rejected(self):
        for text in ("気に" * 40, "palabra " * 100, "ab cd " * 100):
            with self.subTest(text=text[:10]), self.assertRaisesRegex(ValueError, "repetitivo"):
                quality.validate_recognition(text)

    def test_tesseract_is_bounded_and_does_not_use_a_shell(self):
        tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
               "5\t1\t1\t1\t1\t1\t20\t30\t40\t10\t98.5\tTexto\n"
               "5\t1\t1\t1\t1\t2\t65\t30\t45\t10\t97\tnativo\n")
        with patch.object(quality.subprocess, 'run', return_value=SimpleNamespace(stdout=tsv.encode())) as run:
            lines = quality.tesseract_lines(b'bounded-png')
        self.assertEqual(lines[0].text, 'Texto nativo')
        self.assertEqual((lines[0].top, lines[0].bottom), (30, 40))
        options = run.call_args.kwargs
        self.assertEqual(options['timeout'], 30)
        self.assertEqual(options['env']['OMP_THREAD_LIMIT'], '1')
        self.assertEqual(options['env']['OMP_NUM_THREADS'], '1')
        self.assertFalse(options.get('shell', False))
        self.assertEqual(options['input'], b'bounded-png')
        self.assertEqual(run.call_args.args[0][1:3], ['stdin', 'stdout'])

    def test_verifier_missing_timeout_failure_or_malformed_output_fails_closed(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired('tesseract', 30),
                      subprocess.CalledProcessError(1, 'tesseract')):
            with self.subTest(error=error), patch.object(quality.subprocess, 'run', side_effect=error):
                with self.assertRaisesRegex(ValueError, "verificar.*revisión"):
                    quality.tesseract_lines(b'png')
        with patch.object(quality.subprocess, 'run', return_value=SimpleNamespace(stdout=b'invalid')):
            with self.assertRaisesRegex(ValueError, 'inválidos'):
                quality.tesseract_lines(b'png')


class OCRHelperIntegrationTests(unittest.TestCase):
    def test_both_profiles_keep_native_negative_value_when_ocr_drops_sign(self):
        import httpx
        import pymupdf
        from library_service.ocr import extract_page
        recognized = 'La corriente medida del circuito representa 5 amperios.'
        native = 'La corriente medida del circuito representa -5 amperios.'
        response = httpx.Response(200, request=httpx.Request('POST', 'http://gateway.test/v1/chat/completions'),
                                  json={'choices': [{'finish_reason': 'stop', 'message': {'content': recognized}}]})
        for profile in ('glm-ocr', 'lightonocr-2'):
            with self.subTest(profile=profile), pymupdf.open() as pdf, patch.dict(os.environ, {
                'SARA_OCR_MODEL': 'ocr', 'SARA_OCR_PROFILE': profile,
                'LLM_GATEWAY_BASE_URL': 'http://gateway.test/v1', 'LLM_GATEWAY_API_KEY': 'test',
            }), patch('httpx.post', return_value=response), \
                    patch('library_service.ocr.tesseract_lines', return_value=[line(native)]):
                page = pdf.new_page()
                page.insert_text((30, 40), native)
                result = extract_page(page)
            self.assertTrue(result.startswith(recognized))
            self.assertIn(native, result)
            self.assertEqual(result.count('### Texto nativo del PDF conservado'), 1)

    def test_mixed_page_preserves_native_text_and_checks_independent_evidence(self):
        import httpx
        import pymupdf
        from library_service.ocr import extract_page
        body = 'Texto completo reconocido del cuerpo escaneado.'
        response = httpx.Response(200, request=httpx.Request('POST', 'http://gateway.test/v1/chat/completions'),
                                  json={'choices': [{'finish_reason': 'stop', 'message': {'content': body}}]})
        with pymupdf.open() as pdf, patch.dict(os.environ, {
            'SARA_OCR_MODEL': 'ocr', 'SARA_OCR_PROFILE': 'glm-ocr', 'SARA_OCR_MAX_SIDE': '2048',
            'LLM_GATEWAY_BASE_URL': 'http://gateway.test/v1', 'LLM_GATEWAY_API_KEY': 'test',
        }), patch('httpx.post', return_value=response) as post, \
                patch('library_service.ocr.tesseract_lines', return_value=[line(body)]) as verify:
            page = pdf.new_page()
            page.insert_text((30, 40), 'Encabezado nativo irreemplazable')
            result = extract_page(page)
        self.assertTrue(result.startswith(body))
        self.assertIn('Encabezado nativo irreemplazable', result)
        self.assertIn('Texto nativo del PDF conservado', result)
        pixmap = pymupdf.Pixmap(verify.call_args.args[0])
        self.assertLessEqual(max(pixmap.width, pixmap.height), 1600)
        self.assertEqual(post.call_count, 1)

    def test_interior_omission_fails_the_page_despite_successful_http(self):
        import httpx
        import pymupdf
        from library_service.ocr import extract_page
        response = httpx.Response(200, request=httpx.Request('POST', 'http://gateway.test/v1/chat/completions'),
            json={'choices': [{'finish_reason': 'stop', 'message': {'content': 'Respuesta parcial parece completa.'}}]})
        with pymupdf.open() as pdf, patch.dict(os.environ, {
            'SARA_OCR_MODEL': 'ocr', 'SARA_OCR_PROFILE': 'glm-ocr',
            'LLM_GATEWAY_BASE_URL': 'http://gateway.test/v1', 'LLM_GATEWAY_API_KEY': 'test',
        }), patch('httpx.post', return_value=response), patch('library_service.ocr.tesseract_lines',
                return_value=[line('Párrafo interior totalmente omitido durante reconocimiento')]):
            with self.assertRaisesRegex(ValueError, 'página 1.*revisión'):
                extract_page(pdf.new_page())


if __name__ == '__main__':
    unittest.main()
