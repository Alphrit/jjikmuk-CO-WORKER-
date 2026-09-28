"""The public API exposes product-chat features, not operator workflows."""

from app.api.main import app


def test_removed_operations_are_not_registered_as_routes():
    paths = {getattr(route, "path", "") for route in app.routes}

    assert not any(path.startswith("/admin/safety-reviews") for path in paths)
    assert not any(path.startswith("/diagnostics/") for path in paths)
    assert "/admin/rag/rebuild" not in paths
    assert not any(path.endswith("/feedback") for path in paths)
    assert not any(path.endswith("/recheck") for path in paths)
    assert "/products/barcode/{barcode}" not in paths
    assert "/products/report-number/{report_number}" not in paths
    assert "/history/user/{user_id}" not in paths
    assert "/products/{barcode}/report" in paths
