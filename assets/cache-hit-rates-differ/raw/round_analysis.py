#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Round-based cache-hit-rate analysis for pi sessions.
Dimension checks:
  D1  round curve (request index within session)
  D2  session length buckets
  D3  new-token ratio vs round  (mechanism: prefix share)
  D4  head vs tail of session
  D5  what matters more: round/session-length (usage pattern) or model?
Scope: assistant messages with usage (pi footer CH). Missing usage tolerated.
"""
import json, os, glob, sys
from collections import defaultdict

SESS_DIR = "/Users/liuyu/.pi/agent/sessions/"

# model grouping (task asks for 4 curves)
def model_group(m):
    if not m: return "?"
    m = m
    if m == "deepseek-v4-flash": return "deepseek-v4-flash"
    if m == "kimi-for-coding": return "kimi-for-coding"
    if m == "deepseek-v4-pro": return "deepseek-v4-pro"
    if m.startswith("k3"): return "k3*"   # k3, k3-256k, kimi-k3, k3-highspeed
    return "other"

PRIMARY = ["deepseek-v4-flash", "kimi-for-coding", "deepseek-v4-pro", "k3*"]

def ch_of(inp, cr, cw):
    d = inp + cr + cw
    return (cr / d * 100.0) if d > 0 else 0.0

# ---------- scan ----------
files = sorted(glob.glob(os.path.join(SESS_DIR, "**", "*.jsonl"), recursive=True))

sessions = {}        # sessfile -> dict(rounds=[rec], compaction_at=[rounds])
all_recs = []        # flat records
bad_lines = 0
no_usage = 0
asm_total = 0

for f in files:
    cur_model = None
    asm_seq = 0
    seg_model = None
    seg_round = 0
    reqs = []
    comp_rounds = []
    try:
        fh = open(f, "r", errors="replace")
    except OSError:
        continue
    for line in fh:
        line = line.strip()
        if not line: continue
        try:
            d = json.loads(line)
        except Exception:
            bad_lines += 1; continue
        t = d.get("type")
        if t == "model_change":
            cur_model = d.get("modelId") or cur_model
            continue
        if t == "compaction":
            comp_rounds.append(asm_seq)
            continue
        if t != "message": continue
        m = d.get("message")
        if not isinstance(m, dict): continue
        role = m.get("role")
        if role != "assistant": continue
        asm_total += 1
        asm_seq += 1
        model = m.get("model") or cur_model
        if model != seg_model:
            seg_model = model; seg_round = 1
        else:
            seg_round += 1
        u = m.get("usage")
        if not (isinstance(u, dict) and (u.get("input") or 0) > 0):
            no_usage += 1
            continue
        inp = u.get("input", 0) or 0
        cr = u.get("cacheRead", 0) or 0
        cw = u.get("cacheWrite", 0) or 0
        rec = dict(session=f, round=asm_seq, sround=seg_round,
                   group=model_group(model), model=model,
                   input=inp, cr=cr, cw=cw)
        reqs.append(rec)
    fh.close()
    sessions[f] = dict(rounds=reqs, comp=comp_rounds)
    all_recs.extend(reqs)

N = len(all_recs)
print("="*84)
print("PI 会话缓存命中率 —— 轮次/会话长度 维度分析")
print("="*84)
print(f"扫描会话文件 : {len(files)}")
print(f"assistant消息 : {asm_total}  (带 usage 请求 {N}, 缺 usage 跳过 {no_usage}, 坏行 {bad_lines})")
print(f"compaction事件: {sum(len(s['comp']) for s in sessions.values())} 次 (影响 {len([s for s in sessions.values() if s['comp']])} 个会话)")
print()

def pool(recs):
    return dict(input=sum(r['input'] for r in recs),
                cr=sum(r['cr'] for r in recs),
                cw=sum(r['cw'] for r in recs))

# =====================================================================
print("#"*84)
print("# D1  轮次曲线 : 会话内请求序号 vs 命中率 (全模型合并)")
print("#"*84)
# per-round
by_round = defaultdict(list)
for r in all_recs:
    by_round[r['round']].append(r)
maxr = max(by_round)
print(f"会话内最大请求序号: {maxr}   (第1轮应有 N={len(by_round.get(1,[]))} 个请求)")
print()
print(f"{'轮次':>4} {'请求数':>6} {'Σinput':>9} {'ΣcacheRead':>11} {'ΣcacheWrite':>11} {'整体CH%':>8} {'均值CH%':>8} {'新增占比%':>8}")
curve = []
first90 = None
for r in range(1, maxr+1):
    recs = by_round.get(r, [])
    if not recs: continue
    p = pool(recs)
    ch = ch_of(p['input'], p['cr'], p['cw'])
    mean_ch = sum(ch_of(x['input'], x['cr'], x['cw']) for x in recs)/len(recs)
    newr = p['input']/(p['input']+p['cr']+p['cw'])*100 if (p['input']+p['cr']+p['cw'])>0 else 0
    if r <= 30 or r % 10 == 0:
        print(f"{r:>4} {len(recs):>6} {p['input']:>9,} {p['cr']:>11,} {p['cw']:>11,} {ch:>7.2f}% {mean_ch:>7.2f}% {newr:>7.2f}%")
    curve.append((r, ch, len(recs)))
    if first90 is None and ch >= 90.0:
        first90 = r
print(f"...(>30 轮按 10 轮间隔打印, 尾部聚合见下)")
# tail aggregation
for lo, hi, label in [(31,50,"31-50"),(51,100,"51-100"),(101,200,"101-200"),(201,10000,"201+")]:
    recs = [r for r in all_recs if lo <= r['round'] <= hi]
    if not recs: continue
    p = pool(recs)
    print(f"{label:>4} {len(recs):>6} {p['input']:>9,} {p['cr']:>11,} {p['cw']:>11,} {ch_of(p['input'],p['cr'],p['cw']):>7.2f}%")
print()
print(f"★ 第1轮命中率   : {ch_of(pool(by_round[1])['input'], pool(by_round[1])['cr'], pool(by_round[1])['cw']):.2f}%  (整体)  "
      f"cacheRead 非零的第1轮请求: {sum(1 for r in by_round[1] if r['cr']>0)}/{len(by_round[1])}")
print(f"★ 首次进入90%+  : 第 {first90} 轮" if first90 else "★ 首次进入90%+  : 未达到")
# monotonic check on smoothed curve
smooth = []
for i,(r,ch,n) in enumerate(curve):
    lo=max(0,i-1); hi=min(len(curve),i+2)
    s=sum(c[1] for c in curve[lo:hi])/(hi-lo)
    smooth.append(s)
drops_after_5 = [ (curve[i][0], curve[i][1]) for i in range(1,len(curve)) if curve[i][0]>5 and curve[i][1] < curve[i-1][1]-1.0 ]
print(f"★ 单调性         : 第6轮起出现 >1pp 回落的位置数 = {len(drops_after_5)} 个 {drops_after_5[:6]}{'...' if len(drops_after_5)>6 else ''}")
# ASCII curve
print("\n轮次曲线 (整体CH%, 每轮一个点, '.'=该轮):")
pts=[(r,ch) for r,ch,_ in curve if r<=50]
for level in range(100, -4, -5):
    line=f"{level:>3}|"
    for r,ch in pts:
        line += "." if ch>=level-5 else (" " if ch>=level-25 else " ")
    print(line)
print("    +"+"-"*(len(pts)+1))
labs="".join(str(r)[0] if r%5==0 or r==1 else " " for r,_ in pts)
print("     "+labs)
print("     1         5         0         5         0         5         0         5")


# =====================================================================
print()
print("#"*84)
print("# D1b  分模型轮次曲线 (每模型: 轮次 vs 整体CH% / 请求数)")
print("#"*84)
by_rg = defaultdict(lambda: defaultdict(list))
for r in all_recs:
    by_rg[r['group']][r['round']].append(r)
print(f"{'轮次':>4}" + "".join(f" {g:>24}" for g in PRIMARY))
for r in list(range(1,21)) + [25,30,40,50]:
    row = f"{r:>4}"
    for g in PRIMARY:
        recs = by_rg[g].get(r, [])
        if not recs:
            row += f" {'--':>14} {'':>9}"
        else:
            p = pool(recs)
            row += f" {ch_of(p['input'],p['cr'],p['cw']):>5.1f}%({len(recs):>3}req)   "
    print(row)
print()
for g in PRIMARY:
    agg=pool([r for r in all_recs if r['group']==g])
    n=len([r for r in all_recs if r['group']==g])
    print(f"{g:<22} 全部{n:>5}请求 整体CH={ch_of(agg['input'],agg['cr'],agg['cw']):.2f}%   "
          f"其中轮次<=10: {sum(1 for r in all_recs if r['group']==g and r['round']<=10)} 轮次>10: {sum(1 for r in all_recs if r['group']==g and r['round']>10)}")

# =====================================================================
print()
print("#"*84)
print("# D2  会话长度分桶 (按带usage请求数) vs 命中率")
print("#"*84)
buckets = [(1,5),(6,10),(11,20),(21,50),(51,10**9)]
labels = ["1-5","6-10","11-20","21-50","50+"]
print(f"{'桶':>6} {'会话数':>6} {'请求数':>7} {'Σinput':>9} {'ΣcacheRead':>11} {'整体CH%':>8} {'会话均值CH%':>9}")
for (lo,hi),lab in zip(buckets,labels):
    sess_in = [s for s in sessions.values() if lo<=len(s['rounds'])<=hi]
    recs=[r for s in sess_in for r in s['rounds']]
    p=pool(recs)
    sess_mean = sum(ch_of(pool(s['rounds'])['input'],pool(s['rounds'])['cr'],pool(s['rounds'])['cw']) for s in sess_in)/len(sess_in) if sess_in else 0
    print(f"{lab:>6} {len(sess_in):>6} {len(recs):>7} {p['input']:>9,} {p['cr']:>11,} {ch_of(p['input'],p['cr'],p['cw']):>7.2f}% {sess_mean:>8.2f}%")
sess_lens = [len(s['rounds']) for s in sessions.values()]
print(f"(会话长度中位数={sorted(sess_lens)[len(sess_lens)//2]}, 最长={max(sess_lens)}, 单轮会话数={sess_lens.count(1)})")

# =====================================================================
print()
print("#"*84)
print("# D3  前缀占比机制 : 新增token占比 input/(input+cacheRead+cacheWrite) 随轮次递减?")
print("#"*84)
print(f"{'轮次':>4} {'请求数':>6} {'新增占比均值%':>11} {'整体CH%':>8}   (新增占比 + 整体CH ≈ 100 说明前缀全部命中)")
for r in [1,2,3,4,5,6,7,8,9,10,15,20,30,50]:
    recs=by_round.get(r,[])
    if not recs: continue
    p=pool(recs)
    newr=p['input']/(p['input']+p['cr']+p['cw'])*100
    print(f"{r:>4} {len(recs):>6} {newr:>10.2f}% {ch_of(p['input'],p['cr'],p['cw']):>7.2f}%")
print()
print("典型轮次抽样 (单请求示例): 第1/5/10/20轮 各取2个请求展示 input/cacheRead/cacheWrite 与新增占比:")
shown=defaultdict(int)
for r in all_recs:
    if r['round'] in (1,5,10,20) and shown[r['round']]<2:
        p=(r['input'],r['cr'],r['cw'])
        newr=r['input']/sum(p)*100
        print(f"  轮{r['round']:>2} {r['model']:<22} input={r['input']:>7,} cacheRead={r['cr']:>8,} cacheWrite={r['cw']:>7,} 新增占比={newr:>5.1f}%  CH={ch_of(*p):>5.1f}%")
        shown[r['round']]+=1

# =====================================================================
print()
print("#"*84)
print("# D4  会话开头 vs 结尾 (前3条 vs 后3条请求; 会话≥6条)")
print("#"*84)
head_recs=[]; tail_recs=[]; n_sess=0
for s in sessions.values():
    if len(s['rounds'])>=6:
        n_sess+=1
        head_recs+=s['rounds'][:3]
        tail_recs+=s['rounds'][-3:]
p_h=pool(head_recs); p_t=pool(tail_recs)
m_h=sum(ch_of(r['input'],r['cr'],r['cw']) for r in head_recs)/len(head_recs)
m_t=sum(ch_of(r['input'],r['cr'],r['cw']) for r in tail_recs)/len(tail_recs)
print(f"参与会话数: {n_sess}  (前3条请求数 {len(head_recs)}, 后3条请求数 {len(tail_recs)})")
print(f"  前3条: 整体CH={ch_of(p_h['input'],p_h['cr'],p_h['cw']):.2f}%   均值CH={m_h:.2f}%")
print(f"  后3条: 整体CH={ch_of(p_t['input'],p_t['cr'],p_t['cw']):.2f}%   均值CH={m_t:.2f}%")
print(f"  差值(后-前): {ch_of(p_t['input'],p_t['cr'],p_t['cw'])-ch_of(p_h['input'],p_h['cr'],p_h['cw']):+.2f}pp")

# =====================================================================
print()
print("#"*84)
print("# D5  命中率主要由什么决定? 模型 vs 轮次/会话长度")
print("#"*84)
print("(a) 同一模型, 不同轮次(使用方案) —— 前缀长度效应:")
for g in PRIMARY:
    recs1=[r for r in all_recs if r['group']==g and r['round']==1]
    recs2=[r for r in all_recs if r['group']==g and r['round']>=10]
    if not recs1 or not recs2: continue
    p1=pool(recs1); p2=pool(recs2)
    c1=ch_of(p1['input'],p1['cr'],p1['cw']); c2=ch_of(p2['input'],p2['cr'],p2['cw'])
    print(f"  {g:<22} 第1轮 CH={c1:>6.2f}% ({len(recs1)}req)  vs  第10+轮 CH={c2:>6.2f}% ({len(recs2)}req)   差 {c2-c1:+.2f}pp")
print()
print("(b) 同等轮次, 不同模型 —— 模型差异:")
for r in [5,10,20]:
    vals={g: (lambda p: ch_of(p['input'],p['cr'],p['cw']))(pool(by_rg[g].get(r,[]))) for g in PRIMARY}
    vals={g:v for g,v in vals.items() if by_rg[g].get(r)}
    if vals:
        spread=max(vals.values())-min(vals.values())
        print(f"  第{r:>2}轮: " + "  ".join(f"{g}={v:.1f}%" for g,v in sorted(vals.items(),key=lambda kv:-kv[1])) + f"   极差={spread:.1f}pp")
print()
print("(c) 会话长度分桶内 模型构成 (确认长会话效应是否被模型混杂):")
for (lo,hi),lab in zip(buckets,labels):
    recs=[r for s in sessions.values() if lo<=len(s['rounds'])<=hi for r in s['rounds']]
    byg=defaultdict(list)
    for r in recs: byg[r['group']].append(r)
    parts="  ".join(f"{g}:{len(v)}" for g,v in sorted(byg.items(), key=lambda kv:-len(kv[1]))[:3])
    print(f"  {lab:>6} 轮 模型构成: {parts}")

# =====================================================================
print()
print("#"*84)
print("# 附   compaction 对缓存的冲击 (5次事件前后各2个请求)")
print("#"*84)
for f, s in sessions.items():
    for cround in s['comp']:
        rs=s['rounds']
        idx=[i for i,r in enumerate(rs) if r['round']<=cround]
        before=[r for r in rs if r['round'] in (cround-1,cround-2)]
        after=[r for r in rs if r['round'] in (cround+1,cround+2)]
        def fmt(recs):
            if not recs: return "n/a"
            p=pool(recs); return f"CH={ch_of(p['input'],p['cr'],p['cw']):.1f}%"
        print(f"  {os.path.basename(f):<42} compaction@第{cround}条: 前2 {fmt(before):>12}  后2 {fmt(after):>12}")

# =====================================================================
print()
print("#"*84)
print("# 汇总口径 (与上一轮一致, 供对照)")
print("#"*84)
p=pool(all_recs)
print(f"全部带usage请求 {N}, 整体CH = {ch_of(p['input'],p['cr'],p['cw']):.2f}%  (ΣcacheRead/Σ(input+cacheRead+cacheWrite))")
print("="*84)
