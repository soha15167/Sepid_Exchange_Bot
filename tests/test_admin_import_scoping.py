import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADMIN_HANDLER = PROJECT_ROOT / "handlers" / "admin.py"
DATABASE_HELPERS = {"get_advert_offer_joined", "get_euro_advert_by_rowid"}


class AdminImportScopingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(ADMIN_HANDLER.read_text(encoding="utf-8"))
        cls.admin_dashboard_callback = next(
            node
            for node in cls.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "admin_dashboard_callback"
        )

    def test_database_helpers_are_imported_at_module_scope(self):
        module_imports = {
            alias.name
            for node in self.tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "database.db"
            for alias in node.names
        }
        self.assertTrue(DATABASE_HELPERS <= module_imports)

    def test_database_helpers_are_not_reimported_inside_admin_dashboard_callback(self):
        local_imports = {
            alias.name
            for node in ast.walk(self.admin_dashboard_callback)
            if isinstance(node, ast.ImportFrom) and node.module == "database.db"
            for alias in node.names
        }
        self.assertTrue(
            DATABASE_HELPERS.isdisjoint(local_imports),
            "Reimporting these helpers inside admin_dashboard_callback makes them "
            "local variables and breaks earlier branches with UnboundLocalError.",
        )


if __name__ == "__main__":
    unittest.main()
