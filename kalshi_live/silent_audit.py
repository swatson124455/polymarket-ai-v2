"""Find every place the bot SWALLOWS an error, and classify whether anything would notice."""
import ast, sys, io

FILES = ["maker_kalshi_quoter.py", "maker_kalshi_client.py",
         "kalshi_presence_calibrate.py", "kalshi_market_scores.py"]
BASE = (r"C:\Users\samwa\AppData\Local\Temp\claude\C--lockes-picks-polymarket-ai-v2"
        r"\02f270fe-27ab-42e6-8906-2ebc25f6df3b\scratchpad\kalshi-wt\kalshi_live" + "\\")

def body_kind(h, src_lines):
    """What does the handler DO? swallow / count / log / re-raise / return."""
    txt = "\n".join(src_lines[h.lineno-1:h.end_lineno]).lower()
    has_raise = any(isinstance(n, ast.Raise) for n in ast.walk(h))
    has_print = "print(" in txt
    counted = ("+= 1" in txt or "_fail" in txt or "stats[" in txt or "drops[" in txt
               or "qstats" in txt or ".get(" in txt and "+ 1" in txt)
    only_pass = all(isinstance(n, (ast.Pass,)) for n in h.body)
    only_pass_or_continue = all(isinstance(n, (ast.Pass, ast.Continue)) for n in h.body)
    if has_raise: return "RE-RAISES"
    if has_print: return "logs"
    if counted: return "counted"
    if only_pass: return "SILENT (pass)"
    if only_pass_or_continue: return "SILENT (continue)"
    return "returns/other"

rows = []
for f in FILES:
    try:
        src = io.open(BASE+f, encoding="utf-8").read()
    except Exception as e:
        print("skip", f, e); continue
    lines = src.splitlines()
    tree = ast.parse(src)
    # map line -> enclosing function
    funcs = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append((n.lineno, n.end_lineno, n.name))
    for n in ast.walk(tree):
        if isinstance(n, ast.ExceptHandler):
            fn = next((nm for a,b,nm in sorted(funcs) if a <= n.lineno <= b), "<module>")
            bare = n.type is None
            broad = bare or (isinstance(n.type, ast.Name) and n.type.id == "Exception")
            rows.append((f, n.lineno, fn, "bare" if bare else
                         (ast.unparse(n.type) if n.type else "?"),
                         body_kind(n, lines), broad))

kinds = {}
for r in rows: kinds[r[4]] = kinds.get(r[4], 0) + 1
print("EXCEPTION HANDLERS: %d total across %d files\n" % (len(rows), len(FILES)))
for k in sorted(kinds, key=lambda x: -kinds[x]):
    print("  %-18s %d" % (k, kinds[k]))

print("\n--- TRULY SILENT (nothing counted, nothing logged) ---")
sil = [r for r in rows if r[4].startswith("SILENT")]
for f, ln, fn, typ, kind, broad in sorted(sil, key=lambda x: (x[0], x[1])):
    flag = " <<< BARE" if typ == "bare" else ""
    print("  %-26s:%-5d %-30s catches %-12s %s%s" % (f, ln, fn, typ, kind, flag))
print("\n  total truly silent: %d of %d handlers" % (len(sil), len(rows)))
bare = [r for r in rows if r[3] == "bare"]
print("  bare `except:` handlers: %d" % len(bare))
