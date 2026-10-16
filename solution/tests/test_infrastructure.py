import unittest
from pathlib import Path
from ttsolver.sql import Database, literal, tuples
from ttsolver.values import Unknown, truth
from ttsolver.catalog import Catalog

ROOT = Path(__file__).resolve().parents[2]


class InfrastructureTests(unittest.TestCase):
    def test_binary_sql(self):
        self.assertEqual(literal(b"_binary'\xff\\0'"), b'\xff\0')
        self.assertEqual(list(tuples(b"('a,b','x\\\'y'),(NULL,2)")), [['a,b',"x'y"],[None,2]])

    def test_dump(self):
        db = Database(ROOT/'context/db-after-warmup.sql.gz')
        self.assertEqual(len(db.rows('contacts')),5)
        self.assertEqual(len(db.rows('orders')),10)
        clone = db.clone()
        clone.rows('orders').clear()
        self.assertEqual(len(db.rows('orders')),10)

    def test_unknown_never_truthy(self):
        with self.assertRaises(TypeError):
            bool(Unknown('missing DTO input'))
        self.assertIsInstance(truth(Unknown('input')),Unknown)

    def test_source_catalog(self):
        c = Catalog(ROOT/'skeleton/trainticket.json.gz',ROOT/'targets/trainticket')
        self.assertFalse(c.diagnostics)
        mid,_ = c.route('POST','/api/v1/users/login')
        self.assertIn('#getToken(',mid)
        self.assertNotEqual(c.route('DELETE','/api/v1/users/login')[0],mid)
        self.assertIsNotNone(c.route('GET','/api/v1/orderservice/order/123')[0])
        self.assertGreater(len(c.ast),1000)


if __name__ == '__main__':
    unittest.main()
