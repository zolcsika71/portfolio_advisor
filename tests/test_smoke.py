# tests/test_smoke.py

def test_project_import() -> None:
    import portfolio_advisor

    assert portfolio_advisor is not None