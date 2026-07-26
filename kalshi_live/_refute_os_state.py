import kalshi_attribution_ledger as L
from collections import defaultdict
def f(x):
    try: return float(x or 0)
    except: return 0.0
orders = L.get(L.P+"/portfolio/orders?status=resting").get("orders") or []
pos = {p["ticker"]: p for p in (L.get(L.P+"/portfolio/positions").get("market_positions") or [])}
byt = defaultdict(lambda: {"yes":[], "no":[]})
for o in orders:
    t=o.get("ticker"); s=o.get("outcome_side")
    px = f(o.get("yes_price_dollars") if s=="yes" else o.get("no_price_dollars"))
    byt[t][s].append((px, f(o.get("remaining_count_fp")), f(o.get("fill_count_fp")), f(o.get("initial_count_fp"))))
print("TICKER                              rest  pos_fp  realPnL  mktExp | YES ord | NO ord")
for t in sorted(byt):
    y=byt[t]["yes"]; n=byt[t]["no"]
    rest=("Y" if y else "-")+("Y" if n else "-")
    p=pos.get(t,{})
    pf=f(p.get("position_fp")); rp=f(p.get("realized_pnl_dollars")); me=f(p.get("market_exposure_dollars"))
    ystr=";".join(f"{px:.2f}x{rc:.0f}(f{fc:.0f})" for px,rc,fc,ic in y) or "-"
    nstr=";".join(f"{px:.2f}x{rc:.0f}(f{fc:.0f})" for px,rc,fc,ic in n) or "-"
    print(f"{t[:34]:<34} {rest:>4} {pf:7.0f} {rp:8.2f} {me:6.2f} | {ystr:<16} | {nstr}")
