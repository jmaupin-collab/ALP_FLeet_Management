"""Report third-party imports used by backend/app that are missing from requirements.txt."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"

STDLIB = set(sys.stdlib_module_names) | {"__future__"}
FIRST_PARTY = {"app"}
# Bundled with declared FastAPI / uvicorn installs.
TRANSITIVE = {"starlette", "anyio"}

# Import name -> requirement name when they differ
ALIASES = {
    "jose": "PyJWT",
    "jwt": "PyJWT",
    "dotenv": "python-dotenv",
    "pwdlib": "pwdlib",
    "multipart": "python-multipart",
    "pydantic_settings": "pydantic-settings",
    "email_validator": "email-validator",
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "skimage": "scikit-image",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
}


def requirement_names() -> set[str]:
    names: set[str] = set()
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        line = line.split("[", 1)[0]
        for sep in ("==", ">=", "<=", "~=", ">", "<"):
            if sep in line:
                line = line.split(sep, 1)[0]
                break
        names.add(line.strip().lower().replace("_", "-"))
    return names


def top_level(name: str) -> str:
    return name.split(".", 1)[0]


def imported_modules() -> set[str]:
    found: set[str] = set()
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(top_level(alias.name))
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(top_level(node.module))
    return found


def undeclared() -> list[str]:
    declared = requirement_names()
    missing: list[str] = []
    for module in sorted(imported_modules()):
        if module in STDLIB or module in FIRST_PARTY or module in TRANSITIVE:
            continue
        req = ALIASES.get(module, module)
        key = req.lower().replace("_", "-")
        if key not in declared and module.lower().replace("_", "-") not in declared:
            missing.append(module)
    return missing


def main() -> int:
    missing = undeclared()
    if missing:
        print("Undeclared runtime imports:")
        for name in missing:
            print(f"  - {name}")
        return 1
    print("All app runtime imports are declared in requirements.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
