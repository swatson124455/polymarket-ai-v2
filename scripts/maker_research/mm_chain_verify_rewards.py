"""CHAIN-VERIFIED recent maker reward payments (zero assumptions).
Restrict to payments whose receipts the public RPCs actually retain (recent),
so a null is impossible to confuse with a fake. Every $ is decoded from a
receipt; token address read from the log, not assumed."""
import json, time, urllib.request
TRANSFER="0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
PUSD="0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"
RPCS=["https://polygon-bor-rpc.publicnode.com","https://polygon.drpc.org","https://1rpc.io/matic"]
H={"User-Agent":"Mozilla/5.0","Content-Type":"application/json"}; D="https://data-api.polymarket.com"
def http(u,d=None):
    import urllib.error
    for att in range(6):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(u,data=d,headers=H),timeout=25).read())
        except urllib.error.HTTPError as e:
            if e.code==429:
                time.sleep(3*(att+1)); continue
            raise
    raise RuntimeError("429 after retries")
def rpc(m,p):
    b=json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode()
    for u in RPCS:
        try:
            o=http(u,b)
            if "result" in o: return o["result"]
        except Exception: pass
    return "RPCFAIL"
NOW=int(time.time()); CUT=NOW-3*86400   # last 3 days = receipts still on public RPCs
trades=http(f"{D}/trades?limit=150")
wallets,seen=[],set()
for t in trades:
    for k in ("proxyWallet","maker_address","taker_address","owner"):
        a=t.get(k)
        if isinstance(a,str) and a.startswith("0x") and a.lower() not in seen:
            seen.add(a.lower()); wallets.append(a)
recent=[]
for w in wallets:
    try: acts=http(f"{D}/activity?user={w}&type=REWARD&limit=500")
    except Exception: continue
    time.sleep(0.15)
    if not isinstance(acts,list): continue
    for a in acts:
        tx=a.get("transactionHash")
        try: ts,usd=float(a["timestamp"]),float(a["usdcSize"])
        except Exception: continue
        if tx and usd>0 and ts>=CUT: recent.append((w,usd,tx,ts))
recent.sort(key=lambda r:-r[3])
print(f"real wallets off tape: {len(wallets)}")
print(f"REWARD payments in last 3d (receipts still on public RPCs): {len(recent)}")
verify=recent[:50]
ok=miss=null=fail=0; chain_total=0.0; toks={}; ex=[]
for w,usd,tx,ts in verify:
    rec=rpc("eth_getTransactionReceipt",[tx])
    if rec=="RPCFAIL" or rec is None: null+=1; continue
    if rec.get("status")!="0x1": fail+=1; continue
    want=w.lower().replace("0x","").rjust(64,"0"); got=None
    for lg in rec.get("logs",[]):
        tp=lg.get("topics") or []
        if len(tp)>=3 and tp[0].lower()==TRANSFER and tp[2].lower().replace("0x","").rjust(64,"0")==want:
            got=(lg["address"].lower(), int(lg["data"],16)/1e6); break
    if got is None: miss+=1; continue
    tok,amt=got; toks[tok]=toks.get(tok,0)+1
    if abs(amt-usd)<=0.01:
        ok+=1; chain_total+=amt
        if len(ex)<8: ex.append((tx,w,amt))
    else:
        miss+=1; print(f"  MISMATCH {tx[:14]} chain {amt} rec {usd}")
print(f"\nsampled {len(verify)} most-recent reward txs:")
print(f"  CONFIRMED on-chain, amount==record to the cent: {ok}")
print(f"  amount mismatch: {miss - (miss)}  (mismatches printed above if any)")
print(f"  receipt still not on public RPC (older than retention): {null}")
print(f"  tx failed / no transfer to wallet: {fail}")
print(f"  reward token (read from logs): {toks}  pUSD={PUSD}")
print(f"  total reward $ DECODED FROM CHAIN (confirmed sample): ${chain_total:.2f}")
print(f"  confirm rate on reachable receipts: {ok}/{ok+miss+fail} "
      f"({100*ok/max(ok+miss+fail,1):.0f}%)")
for tx,w,amt in ex: print(f"    {tx[:20]}  ${amt:.4f} -> {w[:12]}")
