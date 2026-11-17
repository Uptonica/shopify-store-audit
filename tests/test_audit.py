"""Offline tests: python3 -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills', 'shopify-audit', 'scripts'))
import audit  # noqa: E402
import report  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'products_export.csv')


class ProductExportTest(unittest.TestCase):
    def setUp(self):
        checks, self.info = audit.csv_checks(FIXTURE)
        self.by_id = {c['id']: c for c in checks}

    def test_only_active_products_count(self):
        self.assertEqual(self.info, {'products_in_export': 3, 'active_products': 2})

    def test_alt_text_counted_per_photo(self):
        self.assertEqual(self.by_id['csv_alt_text']['value'], '2 of 3 photos (67%)')

    def test_product_fields(self):
        self.assertEqual(self.by_id['csv_gtin']['evidence'], ['spare-lace'])
        self.assertEqual(self.by_id['csv_vendor']['evidence'], ['spare-lace'])
        self.assertEqual(self.by_id['csv_seo_description']['evidence'], ['spare-lace'])
        self.assertEqual(self.by_id['csv_descriptions']['evidence'], ['spare-lace'])
        self.assertEqual(self.by_id['csv_photos']['evidence'], ['spare-lace'])


class HostileInputTest(unittest.TestCase):
    def test_types_ignore_non_strings(self):
        self.assertEqual(audit.types([{'@type': {'x': 1}}, {'@type': ['Product', 3]}]), {'Product'})

    def test_deep_json_does_not_crash(self):
        self.assertEqual(audit.jsonld('<script type="application/ld+json">' + '[' * 100000 + '</script>'), [])

    def test_robots_blocks(self):
        self.assertEqual(audit.robots_blocks('User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nDisallow: /cart\n'), {'gptbot'})


class ReportTest(unittest.TestCase):
    AUDIT = {'url': 'https://www.example.com', 'title': 'Example', 'summary': {'fail': 1, 'warn': 0, 'unmeasured': 0, 'pass': 3},
             'tools': [{'name': '<b>Evil</b>', 'category': 'email'}], 'audited_at': '2026-11-17T10:00:00Z'}

    def test_store_text_is_escaped(self):
        page = report.render(self.AUDIT, {'store_name': '<script>alert(1)</script>', 'fixes': [{'title': '<img src=x onerror=1>'}]})
        self.assertNotIn('<script>alert', page)
        self.assertNotIn('<img src=x', page)
        self.assertNotIn('<b>Evil', page)

    def test_only_uptonica_links_and_report_medium(self):
        page = report.render(self.AUDIT, {'closing': [
            'See it: https://app.uptonica.com/audit?url=x&utm_source=github&utm_medium=referral&utm_campaign=c',
            'Elsewhere: https://evil.example/phish']})
        self.assertIn('href="https://app.uptonica.com/audit?url=x&amp;utm_source=github&amp;utm_medium=report', page)
        self.assertNotIn('href="https://evil.example', page)
        self.assertIn('3/4', page)

    def test_malformed_audit_does_not_crash_or_inject(self):
        page = report.render({'url': {'x': 1}, 'summary': {'pass': '<script>x</script>'}, 'audited_at': 5, 'tools': 'no'},
                             {'labels': ['x'], 'fixes': 'no', 'closing': ['https://uptonica.com/x" onmouseover="1']})
        self.assertNotIn('<script>x', page)
        self.assertNotIn('" onmouseover', page)

    def test_javascript_store_is_not_a_link(self):
        page = report.render({'url': 'javascript:alert(1)'}, {})
        self.assertNotIn('href="javascript', page)


class LargeExportTest(unittest.TestCase):
    def test_description_over_128kb(self):
        import csv as _csv
        import tempfile
        with tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False, newline='', encoding='utf-8') as fh:
            w = _csv.writer(fh)
            w.writerow(['Handle', 'Title', 'Body (HTML)', 'Vendor', 'Variant Price', 'Status'])
            w.writerow(['big', 'Big', '<p>' + 'word ' * 60000 + '</p>', 'Acme', '1', 'active'])
        try:
            checks, info = audit.csv_checks(fh.name)
        finally:
            os.unlink(fh.name)
        self.assertEqual(info['active_products'], 1)


if __name__ == '__main__':
    unittest.main()
