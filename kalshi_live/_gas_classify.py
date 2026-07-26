import json,collections,datetime as dt
import kalshi_settlement_pnl as S

def parse(ts):
    return dt.datetime.fromisoformat(ts.replace('Z','+00:00'))

fills=json.load(open('snap_task3/fills.json'))
if isinstance(fills,dict): fills=fills.get('fills') or []
setts=json.load(open('snap_task3/settlements.json'))
if isinstance(setts,dict): setts=setts.get('settlements') or []
res_by={s.get('ticker'):s.get('market_result') for s in setts}

# reward windows
win={}
with open('program_universe_20260723T152450Z.jsonl') as fh:
    for line in fh:
        if 'KXAAAGAS' not in line: continue
        line=line.strip().rstrip(',')
        try: p=json.loads(line)
        except: continue
        t=p.get('market_ticker')
        if (t or '').startswith('KXAAAGAS'):
            try: win[t]=(parse(p['start_date']),parse(p['end_date']))
            except: pass

byt=collections.defaultdict(list)
for f in fills:
    t=f.get('ticker') or f.get('market_ticker')
    if t and t.startswith('KXAAAGAS'): byt[t].append(f)

# per-fill window + taker census
census=collections.defaultdict(lambda:{'in_maker':0,'in_taker':0,'out_maker':0,'out_taker':0,'nowin':0})
for t,fl in byt.items():
    w=win.get(t)
    for f in fl:
        ct=parse(f['created_time'])
        tk=f.get('is_taker')
        if not w: census[t]['nowin']+=1; continue
        inw = w[0]<=ct<=w[1]
        key=('in_' if inw else 'out_')+('taker' if tk else 'maker')
        census[t][key]+=1
print("per-contract fill census (in/out window x maker/taker):")
tot=collections.Counter()
for t in sorted(census):
    c=census[t]; tot.update(c)
    print(f"  {t:<28} in_mk={c['in_maker']:>3} in_tk={c['in_taker']:>2} out_mk={c['out_maker']:>2} out_tk={c['out_taker']:>2} nowin={c['nowin']}")
print("TOTALS:",dict(tot))
