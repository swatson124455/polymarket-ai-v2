import json,collections,datetime as dt
import kalshi_settlement_pnl as S
def parse(ts): return dt.datetime.fromisoformat(ts.replace('Z','+00:00'))
fills=json.load(open('snap_task3/fills.json'))
if isinstance(fills,dict): fills=fills.get('fills') or []
byt=collections.defaultdict(list)
for f in fills:
    t=f.get('ticker') or f.get('market_ticker')
    if t and t.startswith('KXAAAGAS'): byt[t].append(f)

# per contract: yes/no buy counts, sells, peak |pos|, final pos, realized, fees, taker realized
print(f"{'ticker':<28}{'yesBuy':>7}{'noBuy':>7}{'peak|p|':>8}{'final':>7}{'realized':>10}{'fees':>7}{'tk#':>4}{'tkReal':>9}")
grand=collections.Counter()
for t,fl in sorted(byt.items()):
    fl2=sorted(fl,key=lambda r:(r.get('created_time') or '',r.get('fill_id') or ''))
    pos=avg=realized=fees=0.0; peak=0.0; yb=nb=0.0; tk_real=0.0; tkn=0; naked_real=0.0; bal_real=0.0
    for f in fl2:
        c=S._f(f.get('count_fp') or f.get('count'))
        q=c if f.get('action')=='buy' else -c
        px=S.fill_yes_price(f); fee=abs(S._f(f.get('fee_cost'))); fees+=fee
        if f.get('outcome_side')=='yes' and f.get('action')=='buy': yb+=c
        if f.get('outcome_side')=='no' and f.get('action')=='buy': nb+=c
        if q==0: continue
        if pos==0 or (pos>0)==(q>0):
            tot=abs(pos)+abs(q); avg=(avg*abs(pos)+px*abs(q))/tot; pos+=q
        else:
            closed=min(abs(pos),abs(q))
            chunk=(px-avg)*closed if pos>0 else (avg-px)*closed
            realized+=chunk
            # naked if inventory before reduction was large (one-sided accumulation), else balanced
            if abs(pos)>10: naked_real+=chunk
            else: bal_real+=chunk
            if f.get('is_taker'): tk_real+=chunk; 
            new=pos+q
            if new==0: avg=0.0
            elif (new>0)!=(pos>0): avg=px
            pos=new
        if f.get('is_taker'): tkn+=1
        peak=max(peak,abs(pos))
    grand['realized']+=realized; grand['fees']+=fees; grand['naked']+=naked_real; grand['bal']+=bal_real; grand['tk_real']+=tk_real
    print(f"{t:<28}{yb:>7.0f}{nb:>7.0f}{peak:>8.1f}{pos:>7.1f}{realized:>10.3f}{fees:>7.3f}{tkn:>4}{tk_real:>9.3f}")
print(f"\nGRAND realized={grand['realized']:.3f} fees={grand['fees']:.3f} fillleg={grand['realized']-grand['fees']:.3f}")
print(f"  naked_realized(|pos|>10 at reduction)={grand['naked']:.3f}  balanced_realized={grand['bal']:.3f}  taker_realized={grand['tk_real']:.3f}")
