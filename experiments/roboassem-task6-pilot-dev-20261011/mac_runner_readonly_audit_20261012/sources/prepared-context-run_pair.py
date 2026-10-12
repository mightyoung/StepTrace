import base64,copy,hashlib,json,pathlib,subprocess,threading,time,urllib.request,zipfile
from jsonschema import Draft202012Validator
from PIL import Image
P=pathlib.Path(__file__).resolve().parent;R=pathlib.Path('/kaggle/working/steptrace-task6');O=R/'results';O.mkdir(exist_ok=True)
assert not (O/'attempts.jsonl').exists(),'Prior attempt ledger exists; no retries allowed'
C=json.loads((P/'execution-input-contract.json').read_text());V=json.loads((R/'runtime-verification.json').read_text())
assert V['commit']=='8345f333951c661d166b00e6f9362e553768f292'
inputs={}
for f in C['model_input_frames']:
 b=(P/f['filename']).read_bytes()
 assert len(b)==f['bytes'] and hashlib.sha256(b).hexdigest()==f['sha256']
 assert Image.open(P/f['filename']).size==(640,480)
 inputs[f['input_id']]=b
assert [r['input_ids'] for r in C['requests']]==[['k04'],['k01','k02','k03','k04']]
req=[];enforcement={}
for spec in C['requests']:
 arm=spec['condition'];schema=C['content_schemas'][arm];Draft202012Validator.check_schema(schema)
 grammar=copy.deepcopy(schema);removed=[]
 def subset(v,path=''):
  if isinstance(v,dict):
   for k in list(v):
    if k in ('contains','minContains','maxContains','uniqueItems','$schema'):removed.append(path+'/'+k);del v[k]
    else:subset(v[k],path+'/'+k)
  elif isinstance(v,list):
   for i,x in enumerate(v):subset(x,path+'/'+str(i))
 subset(grammar);enforcement[arm]={'generation_omitted_keywords':removed,'full_schema_offline_validation_required':True}
 content=[]
 for part in spec['exact_user_content_sequence']:
  if part['type']=='text':content.append(part)
  else:content.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(inputs[part['input_id']]).decode()}})
 body=copy.deepcopy(C['generation_parameters_exact']);body.update(top_p=0.95,top_k=40,cache_prompt=False)
 body['messages']=[{'role':'system','content':C['system_prompt_exact']},{'role':'user','content':content}]
 body['response_format']={'type':'json_schema','json_schema':{'name':'roboassem_task6_visual_state','strict':True,'schema':grammar}}
 req.append((arm,body))
assert req[0][1]['messages'][1]['content'][-1]==req[1][1]['messages'][1]['content'][-1]
params=[{k:v for k,v in b.items() if k not in ('messages','response_format')} for a,b in req];assert params[0]==params[1]
cmd=[str(R/'llama.cpp/build/bin/llama-server'),'-m',str(R/'Qwen3.5-9B-Q4_K_M.gguf'),'--mmproj',str(R/'mmproj-BF16.gguf'),'--no-mmproj-offload','--device','CUDA0','-ngl','99','-c','16384','--parallel','1','--threads','4','--flash-attn','off','--image-min-tokens','1024','--image-max-tokens','2048','--no-context-shift','--no-cache-prompt','--host','127.0.0.1','--port','8080','--top-k','40','--top-p','0.95','--temp','0','--seed','1','--jinja']
config={'runtime':V,'server_args':cmd,'request_parameters':params[0],'schema_enforcement':enforcement,'protocol_commit':'a2cdc0b712ccc70d929a3d0e6b00d6b414dbde98','budget':{'max_requests':2,'retries':0,'parallel':1,'timeout_seconds':60},'state_write_allowed':False}
(O/'execution-config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))
def http(path,b=None,timeout=5):
 data=None if b is None else json.dumps(b,ensure_ascii=False).encode()
 return urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8080'+path,data=data,headers={'Content-Type':'application/json'}),timeout=timeout)
stop=threading.Event()
def monitor():
 with open(O/'gpu-memory.csv','w') as f:
  f.write('epoch,index,memory_used_MiB,memory_total_MiB,utilization_percent\n')
  while not stop.is_set():
   try:
    s=subprocess.check_output(['nvidia-smi','--query-gpu=index,memory.used,memory.total,utilization.gpu','--format=csv,noheader,nounits'],text=True)
    for line in s.splitlines():f.write(str(time.time())+','+line+'\n')
    f.flush()
   except Exception as e:f.write('monitor_error,'+type(e).__name__+'\n')
   stop.wait(1)
log=open(O/'server.log','w');server=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT)
th=threading.Thread(target=monitor);th.start();results=[]
try:
 t=time.monotonic()
 while True:
  if server.poll() is not None:raise RuntimeError('Server exited while loading; inspect server.log')
  try:
   with http('/health') as h:
    if h.status==200:break
  except Exception:pass
  if time.monotonic()-t>240:raise TimeoutError('Loading exceeded 240 seconds; stopping')
  time.sleep(2)
 with http('/props') as h:propsraw=h.read()
 (O/'backend-props.json').write_bytes(propsraw)
 props=json.loads(propsraw); settings=props.get('default_generation_settings',{})
 contexts=[]
 def contexts_walk(v):
  if isinstance(v,dict):
   for k,x in v.items():
    if k in ('n_ctx','n_ctx_per_seq') and isinstance(x,int):contexts.append(x)
    contexts_walk(x)
  elif isinstance(v,list):
   for x in v:contexts_walk(x)
 contexts_walk(props)
 assert contexts and min(contexts)>=16384,('Effective backend context unverified or too small',contexts)
 config['backend_context_values']=contexts
 config['backend_schema_support']='Generation subset only; original contains and uniqueness strictly checked offline'
 (O/'execution-config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))
 # Non-generating text token preflight plus conservative bound on vision tokens.
 for arm,body in req:
  textbody=copy.deepcopy(body)
  textbody['messages'][1]['content']=[x for x in textbody['messages'][1]['content'] if x['type']=='text']
  with http('/apply-template',{'messages':textbody['messages'],'chat_template_kwargs':{'enable_thinking':False}}) as h:templ=json.loads(h.read())
  prompt=templ.get('prompt',templ.get('content'));assert isinstance(prompt,str),templ
  with http('/tokenize',{'content':prompt,'add_special':True}) as h:tokens=json.loads(h.read())['tokens']
  bound=len(tokens)+len(C['requests'][0 if arm=='F' else 1]['input_ids'])*2048+2048+128
  assert bound<=16384,(arm,bound)
  (O/f'preflight-{arm}.json').write_text(json.dumps({'text_tokens':len(tokens),'conservative_context_bound':bound,'context':16384,'image_max_tokens_each':2048,'generation_calls':0}))
 print('PAIR_PREFLIGHT_PASSED_NO_GENERATION_CALLS',flush=True)
 for arm,body in req:
  raw=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode();(O/f'request-{arm}.json').write_bytes(raw)
  a={'condition':arm,'attempt':1,'request_sha256':hashlib.sha256(raw).hexdigest(),'start_epoch':time.time()}
  with open(O/'attempts.jsonl','a') as f:f.write(json.dumps(a)+'\n')
  t=time.monotonic()
  try:
   with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8080/v1/chat/completions',data=raw,headers={'Content-Type':'application/json'}),timeout=60) as h:response=h.read()
   (O/f'response-{arm}-raw.json').write_bytes(response);obj=json.loads(response);choice=obj['choices'][0]
   content=choice['message']['content'];(O/f'response-{arm}-content.txt').write_text(content);parsed=json.loads(content)
   errors=[e.message for e in Draft202012Validator(C['content_schemas'][arm]).iter_errors(parsed)]
   if {r.get('seat_id') for r in parsed.get('current_relations',[])}!={'blue_seat_left','blue_seat_right'}:errors.append('Require exactly one relation for each seat')
   a.update(elapsed_seconds=time.monotonic()-t,response_sha256=hashlib.sha256(response).hexdigest(),response_bytes=len(response),finish_reason=choice['finish_reason'],usage=obj.get('usage'),timings=obj.get('timings'),schema_valid=not errors,schema_errors=errors,content=parsed,truncated=obj.get('truncated','inspect_server_log'))
   assert choice['finish_reason']=='stop','First answer incomplete; no retry'
   pt=obj.get('usage',{}).get('prompt_tokens');assert isinstance(pt,int) and pt+2048<=16384,'Prompt token/no truncation check failed'
   assert not obj.get('truncated',False),'Backend reports truncation'
   print(arm,json.dumps(a,ensure_ascii=False),flush=True)
  except Exception as e:
   a.update(elapsed_seconds=time.monotonic()-t,error=repr(e));results.append(a)
   (O/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));raise
  results.append(a);(O/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
except Exception as e:
 (O/'execution-error.json').write_text(json.dumps({'error':repr(e),'epoch':time.time()}));raise
finally:
 server.terminate()
 try:server.wait(timeout=20)
 except subprocess.TimeoutExpired:server.kill();server.wait()
 stop.set();th.join();log.close()
 count=len((O/'attempts.jsonl').read_text().splitlines()) if (O/'attempts.jsonl').exists() else 0
 (O/'server-stopped.json').write_text(json.dumps({'returncode':server.returncode,'epoch':time.time(),'requests_attempted':count}))
 with zipfile.ZipFile(R/'task6-results.zip','w',zipfile.ZIP_DEFLATED) as z:
  for p in O.iterdir():
   if p.is_file():z.write(p,p.name)
 print('SERVER_STOPPED_REQUEST_COUNT',count,'STOP_KAGGLE_SESSION_IN_UI',flush=True)
