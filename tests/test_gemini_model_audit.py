from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_gemini_models import candidates, list_models, probe  # noqa: E402


class GeminiAuditTests(unittest.TestCase):
    def test_only_text_flash_generate_content_candidates(self):
        names = ['gemini-3.5-flash-lite', 'gemini-4-flash', 'gemini-4-pro',
                 'gemini-4-flash-image', 'text-embedding-004']
        rows = [{'name': 'models/' + name,
                 'supportedGenerationMethods': ['generateContent']} for name in names]
        rows.append({'name': 'models/gemini-5-flash', 'supportedGenerationMethods': ['embedContent']})
        result = candidates(rows)
        self.assertEqual(set(result), {'gemini-3.5-flash-lite', 'gemini-4-flash'})

    def test_transient_error_never_marked_retired(self):
        class Response:
            status_code = 503
        with patch('audit_gemini_models.requests.post', return_value=Response()):
            self.assertEqual(probe('gemini-4-flash', 'dummy'), 'uncertain-503')

    def test_not_found_model_is_unavailable(self):
        class Response:
            status_code = 404
        with patch('audit_gemini_models.requests.post', return_value=Response()):
            self.assertEqual(probe('gemini-2-flash', 'dummy'), 'unavailable-404')

    def test_list_error_never_leaks_key(self):
        from requests import ConnectionError
        with patch('audit_gemini_models.requests.get', side_effect=ConnectionError('https://x?key=SECRET')):
            with self.assertRaises(RuntimeError) as caught:
                list_models('SECRET')
        self.assertNotIn('SECRET', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
