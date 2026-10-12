import hashlib,json,pathlib,shutil,subprocess,tarfile,time,urllib.request
R=pathlib.Path('/kaggle/working/steptrace-task6');R.mkdir(exist_ok=True)
COMMIT='8345f333951c661d166b00e6f9362e553768f292'
REV='3885219b6810b007914f3a7950a8d1b469d598a5'
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(8388608),b''):h.update(b)
 return h.hexdigest()
def get(url,p):
 t=time.monotonic();print('Downloading',p.name,flush=True)
 with urllib.request.urlopen(url,timeout=60) as s,open(p,'wb') as f:shutil.copyfileobj(s,f,8388608)
 print('Downloaded',p.name,p.stat().st_size,round(time.monotonic()-t,2),'s',flush=True)
rec={'runtime_commit':COMMIT,'model_revision':REV,'calls':0,'artifacts':[]}
for name,size,digest in [('Qwen3.5-9B-Q4_K_M.gguf',5680522464,'03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8'),('mmproj-BF16.gguf',921705024,'853698ce7aa6c7ba732478bad280240969ddf7b0fcbf93900046f63903a83383')]:
 p=R/name
 if not p.exists():get(f'https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/{REV}/{name}',p)
 a={'filename':name,'bytes':p.stat().st_size,'sha256':sha(p)}
 assert a['bytes']==size and a['sha256']==digest,a
 rec['artifacts'].append(a);print('SHA256 verified',name,flush=True)
 (R/'download-verification.json').write_text(json.dumps(rec,indent=2))
if not (R/'llama.cpp').exists():
 p=R/'source.tar.gz';get(f'https://github.com/ggml-org/llama.cpp/archive/{COMMIT}.tar.gz',p)
 rec['source_archive_sha256']=sha(p)
 with tarfile.open(p) as t:t.extractall(R,filter='data')
 (R/f'llama.cpp-{COMMIT}').rename(R/'llama.cpp')
(R/'download-verification.json').write_text(json.dumps(rec,indent=2))
print('DOWNLOADS_VERIFIED_NO_INFERENCE',flush=True)
