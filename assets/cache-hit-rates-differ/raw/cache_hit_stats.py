#!/usr/bin/env python3
"""Scan all pi session JSONL files and compute cache hit-rate statistics.
Scope: assistant messages' message.usage (matching pi footer CH metric).
"""
import json, os, glob, sys
from collections import defaultdict

SESS_DIR = "/Users/liuyu/.pi/agent/sessions/"
files = sorted(glob.glob(os.path.join(SESS_DIR, "**", "*.jsonl"), recursive=True))

# ---- accumulators ----
tot = dict(input=0, output=0, cacheRead=0, cacheWrite=0)   # assistant usage
tot_tool = dict(input=0, output=0, cacheRead=0, cacheWrite=0)  # toolResult usage
reqs = 0            # assistant msgs WITH usage
asm_msgs = 0        # all assistant messages
tool_msgs = 0       # all toolResult messages
tool_reqs = 0       # toolResult with usage
no_usage_asm = 0    # assistant msgs missing usage
malformed = 0
bad_lines = 0

buckets = defaultdict(int)       # key -> count
bucket_ids = ["100%", "99-100%", "95-99%", "90-95%", "80-90%", "<80%", "无缓存"]

# grouping
by_group = {}   # group -> dict(input,cacheRead,cacheWrite,count, keys...)
by_dir = {}     # session-dir -> same
by_session = defaultdict(lambda: dict(input=0, cacheRead=0, cacheWrite=0, n=0, ge99=0))
ge99_sessions = defaultdict(list)  # workdir -> list of (sessionfile, count)

providers_seen = defaultdict(int)
models_seen = defaultdict(int)

def bucket_of(hr):
    if hr >= 100: return "100%"
    if hr >= 99: return "99-100%"
    if hr >= 95: return "95-99%"
    if hr >= 90: return "90-95%"
    if hr >= 80: return "80-90%"
    return "<80%"

def add_group(d, g, u, hr, ge99):
    e = d.setdefault(g, dict(input=0, cacheRead=0, cacheWrite=0, n=0, ge99=0))
    e["input"] += u.get("input",0); e["cacheRead"] += u.get("cacheRead",0)
    e["cacheWrite"] += u.get("cacheWrite",0); e["n"] += 1
    if ge99: e["ge99"] += 1

for f in files:
    dname = os.path.basename(os.path.dirname(f))
    try:
        fh = open(f, "r", errors="replace")
    except OSError:
        continue
    # track model/provider state from model_change events
    cur_prov = None; cur_model = None
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            bad_lines += 1
            continue
        t = d.get("type")
        if t == "model_change":
            cur_prov = d.get("provider") or cur_prov
            cur_model = d.get("modelId") or cur_model
            continue
        if t != "message":
            continue
        m = d.get("message")
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        u = m.get("usage")
        prov = m.get("provider") or cur_prov
        model = m.get("model") or cur_model
        if role == "assistant":
            asm_msgs += 1
            if not isinstance(u, dict) or not u.get("input"):
                no_usage_asm += 1
                continue
            inp = u.get("input",0) or 0; out = u.get("output",0) or 0
            cr = u.get("cacheRead",0) or 0; cw = u.get("cacheWrite",0) or 0
            reqs += 1
            tot["input"]+=inp; tot["output"]+=out; tot["cacheRead"]+=cr; tot["cacheWrite"]+=cw
            denom = inp + cr + cw
            hr = (cr / denom * 100.0) if denom > 0 else 0.0
            if cr == 0 and cw == 0 and inp > 0:
                b = "无缓存"
            else:
                b = bucket_of(hr)
            buckets[b] += 1
            ge99 = (hr >= 99.0)
            # group key = provider|model
            key = f"{prov or '?'}|{model or '?'}"
            add_group(by_group, key, u, hr, ge99)
            add_group(by_dir, dname, u, hr, ge99)
            se = by_session[f]
            se["input"]+=inp; se["cacheRead"]+=cr; se["cacheWrite"]+=cw; se["n"]+=1
            if ge99: se["ge99"] += 1
            if prov: providers_seen[prov]+=1
            if model: models_seen[model]+=1
        elif role == "toolResult":
            tool_msgs += 1
            if isinstance(u, dict) and u.get("input"):
                tool_reqs += 1
                tot_tool["input"]+= (u.get("input",0) or 0)
                tot_tool["output"]+= (u.get("output",0) or 0)
                tot_tool["cacheRead"]+= (u.get("cacheRead",0) or 0)
                tot_tool["cacheWrite"]+= (u.get("cacheWrite",0) or 0)
    fh.close()

# ---- report ----
def hr_of(a, cr, cw):
    denom = a + cr + cw
    return (cr/denom*100.0) if denom > 0 else 0.0

overall = hr_of(tot["input"], tot["cacheRead"], tot["cacheWrite"])
overall_tool = hr_of(tot_tool["input"], tot_tool["cacheRead"], tot_tool["cacheWrite"])
n99 = sum(e["ge99"] for e in by_session.values())
ge99_pct = n99/reqs*100 if reqs else 0

print("="*78)
print("PI 会话缓存命中率统计报告")
print("="*78)
print(f"扫描目录      : {SESS_DIR}")
print(f"会话文件数    : {len(files)}")
print(f"总消息行数    : assistant={asm_msgs}, toolResult={tool_msgs}")
print(f"带 usage 的 assistant 请求数(口径A主口径): {reqs}")
print(f"带 usage 的 toolResult 请求数(工具内部LLM): {tool_reqs}")
print(f"assistant 无 usage 消息数: {no_usage_asm}  解析失败行数: {bad_lines}")
print()

print("-"*78)
print("【1. 总览 · 主口径=仅 assistant 消息 usage（与 pi footer CH 一致）】")
print("-"*78)
print(f"  累计 input      : {tot['input']:,}")
print(f"  累计 output     : {tot['output']:,}")
print(f"  累计 cacheRead  : {tot['cacheRead']:,}")
print(f"  累计 cacheWrite : {tot['cacheWrite']:,}")
print(f"  整体命中率 CH   : {overall:.2f}%   (=ΣcacheRead/Σ(input+cacheRead+cacheWrite))")
print(f"  (toolResult 若并入: 请求{tool_reqs}, 累计命中率 {overall_tool:.2f}% )")
print()

print("-"*78)
print("【2. 逐请求命中率分布】(按 pi footer 公式 cacheRead/(input+cacheRead+cacheWrite))")
print("-"*78)
order = ["100%","99-100%","95-99%","90-95%","80-90%","<80%","无缓存"]
print(f"  {'桶':<10}{'数量':>8}{'占比':>9}")
for b in order:
    c = buckets[b]
    print(f"  {b:<10}{c:>8}{c/reqs*100:>8.2f}%")
print()

print("-"*78)
print("【3. 按 provider|model 分组】(仅主口径，Top 15)")
print("-"*78)
print(f"  {'provider|model':<45}{'请求':>6}{'整体CH':>9}")
for g, e in sorted(by_group.items(), key=lambda kv: -kv[1]['n'])[:15]:
    h = hr_of(e['input'], e['cacheRead'], e['cacheWrite'])
    print(f"  {g:<45}{e['n']:>6}{h:>8.2f}%")
print()

print("-"*78)
print("【3b. 按会话目录分组】(Top 15)")
print("-"*78)
print(f"  {'session-dir':<55}{'请求':>6}{'整体CH':>9}")
for g, e in sorted(by_dir.items(), key=lambda kv: -kv[1]['n'])[:15]:
    h = hr_of(e['input'], e['cacheRead'], e['cacheWrite'])
    print(f"  {g:<55}{e['n']:>6}{h:>8.2f}%")
print()

print("-"*78)
print(f"【4. 命中率≥99% 的请求】")
print("-"*78)
print(f"  请求数  : {n99} / {reqs}  ({ge99_pct:.2f}%)")
print(f"  这些请求分布在 {len([s for s in by_session if by_session[s]['ge99']>0])} 个会话文件中")
# group by workdir
bywd = defaultdict(lambda: dict(sessions=set(), reqs=0))
for f, e in by_session.items():
    if e['ge99']>0:
        wd = os.path.basename(os.path.dirname(f))
        bywd[wd]['sessions'].add(f)
        bywd[wd]['reqs'] += e['ge99']
print(f"  按工作目录汇总 (会话数=含≥99%请求的会话文件数):")
print(f"  {'workdir':<58}{'会话数':>6}{'≥99%请求':>9}")
for wd, e in sorted(bywd.items(), key=lambda kv: -kv[1]['reqs']):
    print(f"  {wd:<58}{len(e['sessions']):>6}{e['reqs']:>9}")
print()

print("-"*78)
print("【5. 数据量】")
print("-"*78)
print(f"  会话文件数 : {len(files)}")
print(f"  assistant消息数 : {asm_msgs} (其中带usage {reqs})")
print(f"  toolResult消息数: {tool_msgs} (其中带usage {tool_reqs})")
print(f"  解析失败行数: {bad_lines}")
print()

# conclusion
print("="*78)
print("【结论】")
print("="*78)
if reqs and overall >= 99.0:
    verdict = "✅ 可以支撑"
elif reqs and ge99_pct >= 99.0:
    verdict = "⚠️ 整体累计CH未达99%，但逐请求≥99%占比达标"
elif reqs and overall >= 95.0:
    verdict = "⚠️ 未达到99%（介于95-99%）"
else:
    verdict = "❌ 不能支撑"
print(f"  整体累计命中率: {overall:.2f}%  |  ≥99%请求占比: {ge99_pct:.2f}%")
print(f"  判定: {verdict}")
print("  口径说明: 主口径仅统计 assistant 消息的 message.usage（与 pi TUI footer 的 CH 一致）；")
print("            toolResult 的 usage 为工具内部 LLM 调用产生的 tokens，已单独统计但未并入主口径。")
print("="*78)
