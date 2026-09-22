from scripts.check_undeclared_imports import undeclared


def test_app_runtime_imports_are_declared():
    missing = undeclared()
    assert missing == [], f"Add these packages to requirements.txt: {missing}"
