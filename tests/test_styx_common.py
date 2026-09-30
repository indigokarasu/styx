#!/usr/bin/env python3
"""Unit tests for Styx's pure helpers and DB primitives.

WHY these exist: every one of these functions decides whether an obfuscated
bank string becomes a usable merchant, so a silent regression here is
invisible in the pipeline output — `Enriched: N` still prints. These tests
pin the exact behaviours the enrichment pipeline depends on.

Scope: pure functions plus DB primitives against a temp DB. No network, no
real database, no Plaid, no Google Places. Runs in well under a second so it
is safe to run inside a cron audit.

Usage:
    python3 -m unittest discover -s tests        # from the skill root
    python3 tests/test_styx_common.py            # direct
    python3 tests/test_styx_common.py --help     # usage, exits 0, writes nothing
"""
import os
import sys
import tempfile
import unittest

_HELP_ARGS = {"--help", "-h"}
if set(sys.argv[1:]) & _HELP_ARGS:
    print((__doc__ or "").strip())
    sys.exit(0)

# scripts/ is a sibling of tests/, not on sys.path under `unittest discover -s tests`.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import styx_common as sc  # noqa: E402
import styx_universal_enrich as sue  # noqa: E402


class TestNormalize(unittest.TestCase):
    def test_strips_punctuation_and_case(self):
        self.assertEqual(sc.normalize("DD *DOORDASH ROYALINDI"), "dd doordash royalindi")
        self.assertEqual(sc.normalize("SQ *Blue Bottle 1234"), "sq blue bottle 1234")

    def test_collapses_whitespace(self):
        self.assertEqual(sc.normalize("WHOLE   FOODS\tMARKET "), "whole foods market")

    def test_empty_is_empty_not_none(self):
        # A None return would break the UNIQUE(normalized_name) lookup path.
        self.assertEqual(sc.normalize(""), "")
        self.assertEqual(sc.normalize(None), "")


class TestIsRedacted(unittest.TestCase):
    def test_fully_asterisked_is_redacted(self):
        self.assertTrue(sc.is_redacted("***********"))
        self.assertTrue(sc.is_redacted("  ***  "))

    def test_dense_asterisks_are_redacted(self):
        # >30% asterisks: a real name never looks like this.
        self.assertTrue(sc.is_redacted("AB***CD***EF"))

    def test_sparse_asterisk_keeps_name(self):
        # The Plaid prefix form: enough signal to match on.
        self.assertFalse(sc.is_redacted("DD *DOORDASH ROYALINDI"))

    def test_empty_is_redacted(self):
        self.assertTrue(sc.is_redacted(""))
        self.assertTrue(sc.is_redacted(None))


class TestCategoryMap(unittest.TestCase):
    def test_finance_and_food(self):
        self.assertEqual(sc.CATEGORY_MAP["FOOD_AND_DRINK"], "restaurant")
        self.assertEqual(sc.CATEGORY_MAP["LOAN_PAYMENTS"], "finance")

    def test_skip_categories_use_the_stored_lowercase_namespace(self):
        # Two namespaces, deliberately: CATEGORY_MAP is keyed on Plaid's
        # UPPERCASE personal_finance_category, while merchants.category stores
        # the lowercased value that enrichment actually writes. SKIP_CATEGORIES
        # filters on the stored column, so it must be lowercase — if someone
        # 'fixes' it to uppercase to match CATEGORY_MAP, every skipped row stops
        # being skipped and the pipeline spends Places quota on transfers.
        upper = {k for k in sc.CATEGORY_MAP}
        for cat in sue.SKIP_CATEGORIES:
            self.assertNotIn(cat, upper, f"{cat} is uppercase; SKIP_CATEGORIES filters the stored column")
        for cat in sue.SKIP_CATEGORIES:
            self.assertIn(cat.lower(), sue.SKIP_CATEGORIES, "SKIP_CATEGORIES must already be lowercase")


class TestParseFormattedAddress(unittest.TestCase):
    def test_us_street_with_zip(self):
        self.assertEqual(
            sue.parse_formatted_address("525 Market St, San Francisco, CA 94105, USA"),
            ("San Francisco", "CA", "94105"),
        )

    def test_us_city_only(self):
        self.assertEqual(
            sue.parse_formatted_address("Berkeley, CA, USA"), ("Berkeley", "CA", None)
        )

    def test_uk_postcode(self):
        # Stated as 'UK' in the state slot on purpose: the script collapses
        # the region out of UK addresses and keeps the postcode.
        self.assertEqual(
            sue.parse_formatted_address("92 Station Rd., Soham, ELY CB7 5DZ, UK"),
            ("Soham", "UK", "CB7 5DZ"),
        )

    def test_unparseable_returns_nones(self):
        self.assertEqual(sue.parse_formatted_address(""), (None, None, None))
        self.assertEqual(sue.parse_formatted_address("Nowhere"), (None, None, None))


class TestCleanName(unittest.TestCase):
    def test_strips_plaid_prefixes(self):
        self.assertEqual(sue.clean_name("ABM-350 MISSION GARAGE"), "350 MISSION GARAGE")
        self.assertEqual(sue.clean_name("SQ *Blue Bottle 1234"), "Blue Bottle 1234")

    def test_greedy_delivery_prefix_eats_the_brand(self):
        # KNOWN DEFECT, pinned so it cannot change silently. The regex
        # alternative `DD\s+\*DOORDASH\s+` is greedy, so it consumes the brand
        # token it was meant to preserve: "DD *DOORDASH ROYALINDI" -> "ROYALINDI".
        # That is a worse query than the original string, and the merchant can
        # then never reach source='google_places'. Confirmed against the live
        # DB: 760 merchants carry source='google_places' and ZERO DD*-prefixed
        # ones do. Fixing this changes production query behaviour, so it is
        # documented in references/enrichment-known-issues.md rather than
        # silently patched here.
        self.assertEqual(sue.clean_name("DD *DOORDASH ROYALINDI"), "ROYALINDI")

    def test_trailing_uppercase_suffix_truncation(self):
        # The "strip a short truncated tail" rule eats ALL-CAPS location
        # markers: "SP THANKS ICON" -> "THANKS". Also pinned, same reason.
        self.assertEqual(sue.clean_name("SP THANKS ICON"), "THANKS")

    def test_never_returns_empty(self):
        # Returning '' would send an empty textQuery to Places and waste quota.
        self.assertTrue(sue.clean_name("DD *"))


class TestDbPrimitives(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="styx-test-")
        self.db = os.path.join(self.tmp, "styx.db")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        # Confine to what this test created: the invariant is 'this module made
        # it', not 'it is under /tmp'.
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_creates_all_three_core_tables(self):
        conn = sc.init_styx_db(self.db)
        self.addCleanup(conn.close)
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"merchants", "transaction_merchants", "enrichment_runs"} <= names)

    def test_init_is_idempotent(self):
        # seed.py re-runs against a live DB; a second init must not raise.
        sc.init_styx_db(self.db).close()
        sc.init_styx_db(self.db).close()

    def test_get_or_create_is_idempotent_on_normalized_name(self):
        conn = sc.init_styx_db(self.db)
        self.addCleanup(conn.close)
        a, created_a = sc.get_or_create_merchant(conn, "Blue Bottle Coffee")
        b, created_b = sc.get_or_create_merchant(conn, "blue bottle coffee!")
        self.assertTrue(created_a)
        self.assertFalse(created_b, "punctuation variant must resolve to the same row")
        self.assertEqual(a, b)

    def test_link_transaction_upserts_instead_of_failing(self):
        # re-seeding the same day must not blow up on the UNIQUE constraint.
        conn = sc.init_styx_db(self.db)
        self.addCleanup(conn.close)
        mid, _ = sc.get_or_create_merchant(conn, "Zuni Cafe")
        sc.link_transaction(conn, "tx-1", mid, "ZUNI*CAFE", "exact", 0.9)
        sc.link_transaction(conn, "tx-1", mid, "ZUNI*CAFE", "exact", 0.9)
        n = conn.execute("SELECT COUNT(*) FROM transaction_merchants").fetchone()[0]
        self.assertEqual(n, 1)


class TestLoadEnv(unittest.TestCase):
    def test_parses_and_ignores_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "x.env")
            with open(p, "w") as f:
                f.write("# comment\n\nPLAID_CLIENT_ID=abc\nBAD LINE\n")
            env = sc.load_env(p)
        self.assertEqual(env["PLAID_CLIENT_ID"], "abc")
        self.assertNotIn("BAD LINE", env)


if __name__ == "__main__":
    unittest.main()
