import json
import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.clean_data import clean_dataset, parse_date, parse_decimal


class CleaningTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "data").mkdir()
        (self.root / "data" / "stores.csv").write_text(
            "store_id,store_name,category,district\nS01,门店,轻食,上海\n", encoding="utf-8"
        )
        (self.root / "data" / "products.csv").write_text(
            "product_id,product_name,product_category,unit_price\nP01,商品,主食,10.00\nP02,商品2,主食,5.00\n", encoding="utf-8"
        )
        self.sales = self.root / "data" / "sales.csv"
        self.original = """order_id,date,store_id,product_id,qty,amount,payment
O1,2026-08-18,S01,P01,1,¥10.00,微信
O1,2026/08/18,S01,P02,2,"1,0",微信
O1,2026-08-18,S01,P01,1,¥10.00,微信
O2,18-08-2026,S01,P01,-1,-10,现金
O3,2026-08-18,S99,P01,1,10,微信
O4,2026-08-20,S01,P01,1,10,微信
O5,2026-08-18,S01,P01,0,10,微信
O6,2026-08-18,S01,P01,1,,微信
"""
        self.sales.write_text(self.original, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_parsers(self):
        self.assertEqual(parse_date("2026/08/18")[0], "2026-08-18")
        self.assertEqual(parse_date("18-08-2026")[0], "2026-08-18")
        self.assertEqual(parse_date("2026-08-20")[1], "FUTURE_DATE")
        self.assertEqual(str(parse_decimal("¥1,234.50", money=True)), "1234.50")

    def test_cleaning_outputs_and_metrics(self):
        source_hash = hashlib.sha256(self.sales.read_bytes()).hexdigest()
        report = clean_dataset(self.root)
        db_path = self.root / "data" / "clean" / "sales_clean.sqlite"
        self.assertTrue(db_path.exists())
        db = sqlite3.connect(db_path)
        try:
            valid = db.execute("SELECT COUNT(*) FROM sales_clean").fetchone()[0]
            issues = db.execute("SELECT error_type, COUNT(*) FROM quarantine GROUP BY error_type").fetchall()
            unmatched = db.execute(
                "SELECT COUNT(*) FROM sales_clean s LEFT JOIN stores_clean st ON s.store_id = st.store_id "
                "LEFT JOIN products_clean p ON s.product_id = p.product_id "
                "WHERE st.store_id IS NULL OR p.product_id IS NULL"
            ).fetchone()[0]
        finally:
            db.close()
        self.assertEqual(valid, 3)  # O1 x2 and O2 refund; exact duplicate removed.
        self.assertEqual(report["order_count"], 2)
        self.assertAlmostEqual(report["net_revenue"], 10.0)
        self.assertIn(("INVALID_STORE_FK", 1), issues)
        self.assertIn(("MISSING_VALUE", 1), issues)
        self.assertEqual(unmatched, 0)
        self.assertEqual(self.sales.read_text(encoding="utf-8"), self.original)
        self.assertEqual(hashlib.sha256(self.sales.read_bytes()).hexdigest(), source_hash)
        report_file = self.root / "data" / "reports" / "cleaning_report.json"
        self.assertEqual(json.loads(report_file.read_text(encoding="utf-8"))["valid_rows"], 3)
        self.assertEqual(clean_dataset(self.root), report)


if __name__ == "__main__":
    unittest.main()
