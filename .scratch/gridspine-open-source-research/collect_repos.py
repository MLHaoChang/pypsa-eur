import base64,concurrent.futures,json,subprocess,datetime
from pathlib import Path
root=Path(__file__).resolve().parent / 'evidence'
root.mkdir(parents=True, exist_ok=True)
repos=['PyPSA/PyPSA','e2nIEE/pandapower','DSS-Extensions/OpenDSSDirect.py','CURENT/andes','OpenIPSL/OpenIPSL','NatLabRockies/REopt.jl','NatLabRockies/ssc','NatLabRockies/pysam','NatLabRockies/BLAST-Lite','pybamm-team/PyBaMM','NatLabRockies/PRAS','do-mpc/do-mpc','python-control/python-control','CATIA-Systems/FMPy','GMLC-TDC/HELICS','oemof/demandlib','RAMP-project/RAMP','HewlettPackard/dc-rl','HewlettPackard/sustain-cluster','oemof/tespy','IDAES/idaes-pse','pvlib/pvlib-python','wind-python/windpowerlib','PyPSA/atlite','FZJ-IEK3-VSA/tsam','gridstatus/gridstatus','EnergieID/entsoe-py','open-energy-transition/pypsa-ppa','EllieKallmier/ppa_analysis','OpenLEADR/openleadr-python','Sienna-Platform/PowerSimulationsDynamics.jl','NatLabRockies/PVDegradationTools']
def api(path):
 p=subprocess.run(['gh','api',path],capture_output=True,text=True,timeout=60)
 if p.returncode:return {'error':p.stderr[:350]}
 return json.loads(p.stdout)
def collect(repo):
 data=api('repos/'+repo)
 if 'error' in data:return {'requested':repo,**data}
 canonical=data['full_name'];slug=canonical.replace('/','__')
 (root/(slug+'.metadata.json')).write_text(json.dumps(data,indent=2)+'\n')
 readme=api('repos/'+canonical+'/readme')
 if readme.get('content'):
  text=base64.b64decode(readme['content']).decode('utf-8',errors='replace')
  (root/(slug+'.README.md')).write_text(text)
 license=api('repos/'+canonical+'/license')
 if license.get('content'):(root/(slug+'.LICENSE.txt')).write_bytes(base64.b64decode(license['content']))
 release=api('repos/'+canonical+'/releases/latest')
 row={k:data.get(k) for k in ['full_name','html_url','description','archived','disabled','pushed_at','updated_at','stargazers_count','open_issues_count','default_branch','language']}
 row['license']=data.get('license');row['license_file']=license.get('html_url');row['latest_release']={k:release.get(k) for k in ['tag_name','published_at','html_url']};row['readme_url']=readme.get('html_url')
 return row
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
 result=list(pool.map(collect,repos))
(root/'repository-inventory.json').write_text(json.dumps({'retrieved_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'repositories':result},indent=2)+'\n')
for r in result:
 lic=r.get('license')or{}
 print(json.dumps({'repo':r.get('full_name',r.get('requested')),'license':lic.get('spdx_id'),'archived':r.get('archived'),'push':r.get('pushed_at'),'release':r.get('latest_release'),'error':r.get('error')}))
