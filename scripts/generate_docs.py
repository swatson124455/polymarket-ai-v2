#!/usr/bin/env python3
"""
Generate API docs from docstrings (AST). Outputs markdown under docs/api/.
Run from project root: python scripts/generate_docs.py
"""
import ast
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def extract_docstrings(module_path: Path) -> dict:
    """Extract module, class, and function docstrings from a Python file."""
    try:
        text = module_path.read_text(encoding="utf-8")
    except Exception:
        return {"module": None, "classes": {}, "functions": {}}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {"module": None, "classes": {}, "functions": {}}
    docs = {
        "module": ast.get_docstring(tree),
        "classes": {},
        "functions": {},
    }
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = {}
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods[item.name] = ast.get_docstring(item)
            docs["classes"][node.name] = {
                "docstring": ast.get_docstring(node),
                "methods": methods,
            }
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            docs["functions"][node.name] = ast.get_docstring(node)
    return docs


def generate_markdown(docs: dict, module_name: str) -> str:
    """Turn extracted docs into markdown."""
    md = [f"# {module_name}\n"]
    if docs.get("module"):
        md.append(f"{docs['module']}\n")
    if docs.get("classes"):
        md.append("## Classes\n")
        for class_name, class_doc in docs["classes"].items():
            md.append(f"### {class_name}\n")
            if class_doc.get("docstring"):
                md.append(f"{class_doc['docstring']}\n")
            if class_doc.get("methods"):
                md.append("#### Methods\n")
                for method_name, method_doc in class_doc["methods"].items():
                    md.append(f"##### `{method_name}()`\n")
                    if method_doc:
                        md.append(f"{method_doc}\n")
    if docs.get("functions"):
        md.append("## Functions\n")
        for func_name, func_doc in docs["functions"].items():
            md.append(f"### `{func_name}()`\n")
            if func_doc:
                md.append(f"{func_doc}\n")
    return "\n".join(md)


def main() -> int:
    src_dirs = [_project_root / "base_engine", _project_root / "config"]
    docs_dir = _project_root / "docs" / "api"
    docs_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for src_dir in src_dirs:
        if not src_dir.exists():
            continue
        for py_file in src_dir.rglob("*.py"):
            if py_file.name.startswith("__"):
                continue
            rel = py_file.relative_to(_project_root)
            module_name = str(rel).replace("\\", "/").replace("/", ".").replace(".py", "")
            docs = extract_docstrings(py_file)
            if not docs["module"] and not docs["classes"] and not docs["functions"]:
                continue
            md = generate_markdown(docs, module_name)
            out_file = docs_dir / f"{rel.with_suffix('.md')}"
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(md, encoding="utf-8")
            print(f"  {rel} -> docs/api/{rel.with_suffix('.md')}")
            count += 1
    print(f"\nGenerated {count} API doc files in docs/api/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
