import hashlib,json,pathlib,subprocess,time,shutil
R=pathlib.Path('/kaggle/working/steptrace-task6');S=R/'llama.cpp'
assert shutil.which('nvcc'),'No CUDA compiler; stop without inference'
assert json.loads((R/'download-verification.json').read_text())['calls']==0
t=time.monotonic()
for i,cmd in enumerate([['cmake','-S',str(S),'-B',str(S/'build'),'-DGGML_CUDA=ON','-DCMAKE_CUDA_ARCHITECTURES=75','-DLLAMA_CURL=OFF','-DLLAMA_BUILD_TESTS=OFF','-DLLAMA_BUILD_EXAMPLES=OFF','-DLLAMA_BUILD_SERVER=ON','-DCMAKE_BUILD_TYPE=Release'],['cmake','--build',str(S/'build'),'--target','llama-server','-j','4']]):
 print('Build',i,flush=True)
 with open(R/f'build-{i}.log','w') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,timeout=480,check=True)
p=S/'build/bin/llama-server'
v=subprocess.check_output([str(p),'--version'],stderr=subprocess.STDOUT,text=True)
helptext=subprocess.check_output([str(p),'--help'],stderr=subprocess.STDOUT,text=True)
for flag in ['--no-mmproj-offload','--image-max-tokens','--no-context-shift','--no-cache-prompt']:assert flag in helptext,flag
(R/'server-help.txt').write_text(helptext)
(R/'runtime-verification.json').write_text(json.dumps({'commit':'8345f333951c661d166b00e6f9362e553768f292','binary_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'version':v,'build_seconds':time.monotonic()-t},indent=2))
print(v,'BUILD_VERIFIED_NO_INFERENCE',flush=True)
