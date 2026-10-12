from pathlib import Path
import hashlib, json, runpy
pkg = Path('/kaggle/input/datasets/amurdaddy/steptrace-task6-private-inputs-v1-20261011')
src = (pkg / 'run_pair.py').read_text()
assert hashlib.sha256(src.encode()).hexdigest() == '9cb830588290d28b730f5dde1ce096d3e2ba43d6198bffcc79fe1ad2aabcf985'
old = "with http('/props') as h:(O/'backend-props.json').write_bytes(h.read())"
new = "with http('/props') as h:propsraw=h.read()\n (O/'backend-props.json').write_bytes(propsraw)\n props=json.loads(propsraw); settings=props.get('default_generation_settings',{})\n contexts=[]\n def contexts_walk(v):\n  if isinstance(v,dict):\n   for k,x in v.items():\n    if k in ('n_ctx','n_ctx_per_seq') and isinstance(x,int):contexts.append(x)\n    contexts_walk(x)\n  elif isinstance(v,list):\n   for x in v:contexts_walk(x)\n contexts_walk(props)\n assert contexts and min(contexts)>=16384,('Effective backend context unverified or too small',contexts)\n config['backend_context_values']=contexts\n config['backend_schema_support']='Generation subset only; original contains and uniqueness strictly checked offline'\n (O/'execution-config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))"
assert src.count(old) == 1
src = src.replace(old, new)
out = Path('/kaggle/working/steptrace-task6/run_pair-effective-context-check.py')
out.write_text(src)
assert not (out.parent / 'results/attempts.jsonl').exists(), 'Prior attempt exists; no retries allowed'
_ = runpy.run_path(str(out))
