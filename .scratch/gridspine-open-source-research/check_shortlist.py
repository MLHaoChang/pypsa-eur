import subprocess,json,concurrent.futures
from pathlib import Path
root=Path(__file__).resolve().parent / 'evidence'
root.mkdir(parents=True, exist_ok=True)
repos=['NatLabRockies/BLAST-Lite','oemof/oemof-demand','NatLabRockies/pysam','CURENT/andes','HewlettPackard/sustain-cluster','CATIA-Systems/FMPy','NatLabRockies/PRAS','do-mpc/do-mpc','python-control/python-control','open-energy-transition/pypsa-ppa','opendsm/opendsm','dss-extensions/OpenDSSDirect.py']
def get(repo):
 meta_path=root/(repo.replace('/','__')+'.metadata.json')
 meta=json.loads(meta_path.read_text()) if meta_path.exists() else json.loads(subprocess.check_output(['gh','api','repos/'+repo],text=True))
 branch=meta['default_branch'];p=subprocess.run(['gh','api',f'repos/{repo}/git/trees/{branch}?recursive=1'],capture_output=True,text=True,timeout=60)
 if p.returncode:return {'repo':repo,'error':p.stderr[:150]}
 data=json.loads(p.stdout);paths=[x['path'] for x in data.get('tree',[]) if x['type']=='blob']
 (root/(repo.replace('/','__')+'.tree.json')).write_text(json.dumps(data,indent=2)+'\n')
 tests=[x for x in paths if ('test' in x.lower() or 'pytest' in x.lower()) and x.endswith(('.py','.jl','.m','.toml','.ini'))]
 workflows=[x for x in paths if x.startswith('.github/workflows/')]
 return {'repo':repo,'tree_sha':data.get('sha'),'truncated':data.get('truncated'),'tests_count':len(tests),'test_examples':tests[:5],'workflows':workflows[:8],'package_files':[x for x in paths if x in ['pyproject.toml','setup.py','Project.toml','requirements.txt','environment.yml','environment.yaml']]}
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(get,repos))
(root/'shortlist-engineering-signals.json').write_text(json.dumps(rows,indent=2)+'\n')
for r in rows:print(json.dumps(r))
