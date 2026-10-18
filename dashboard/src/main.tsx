import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Activity, ArrowRight, Check, CircleHelp, Cpu, GitBranch, Layers3, Lock, RefreshCw, Server, ShieldCheck, X} from 'lucide-react';
import './style.css';

type Project = {id:string;name:string;repo_url:string;default_branch?:string};
type Node = {id:string;name:string;tags:string[];models:string[];platform:string;online:boolean;last_seen:string};
type Task = {id:string;key:string;status:string;risk:string;approved:boolean;model:string;kind:string;depends_on:string[];output?:string;attempts:number;run_id:string};
type Run = {id:string;project_id:string;title:string;status:string;created_at:string};
type Event = {id:string;kind:string;detail:string;created_at:string;task_id:string|null};
type Overview = {projects:number;runs:number;nodes:number;pending_approvals:number};

function App() {
  const [token,setToken]=useState(()=>{
    try { return window.sessionStorage.getItem('oho-admin-token') ?? ''; }
    catch { return ''; }
  });
  const [inputToken,setInputToken]=useState('');
  const [active,setActive]=useState<'overview'|'runs'|'nodes'|'approvals'|'events'|'projects'>('overview');
  const [overview,setOverview]=useState<Overview>({projects:0,runs:0,nodes:0,pending_approvals:0});
  const [projects,setProjects]=useState<Project[]>([]);
  const [runs,setRuns]=useState<Run[]>([]);
  const [nodes,setNodes]=useState<Node[]>([]);
  const [approvals,setApprovals]=useState<Task[]>([]);
  const [events,setEvents]=useState<Event[]>([]);
  const [detail,setDetail]=useState<{run:Run;tasks:Task[]}|null>(null);
  const [selected,setSelected]=useState<string|null>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [projectName,setProjectName]=useState('');
  const [projectRepo,setProjectRepo]=useState('');
  const [showCreate,setShowCreate]=useState(false);
  const [newRun,setNewRun]=useState({project_id:'',title:'',tasks:'[\n  {"key":"test","kind":"command","argv":["pytest","-q"]}\n]'});
  const [showRun,setShowRun]=useState(false);

  async function request<T>(path:string, init:RequestInit={}, providedToken=token):Promise<T>{
    const headers = new Headers(init.headers);
    headers.set('Authorization', 'Bearer '+providedToken);
    if(init.body)headers.set('Content-Type','application/json');
    const resp=await fetch('/api/v1'+path,{...init,headers,cache:'no-store'});
    if(!resp.ok) {
      const msg=await resp.text();
      if(resp.status===401) {
        try { window.sessionStorage.removeItem('oho-admin-token'); } catch {}
        setToken('');
        setError('Phiên đăng nhập hết hạn hoặc token không hợp lệ. Vui lòng đăng nhập lại.');
      }
      throw Error(resp.status+' '+msg.slice(0,250));
    }
    return await resp.json() as T;
  }
  async function load(auth=token){
    if(!auth)return;
    try{
      const [o,p,r,n,a,e]=await Promise.all([
        request<Overview>('/overview',{},auth),
        request<Project[]>('/projects',{},auth),
        request<Run[]>('/runs',{},auth),
        request<Node[]>('/nodes',{},auth),
        request<Task[]>('/approvals',{},auth),
        request<Event[]>('/events',{},auth)]);
      setOverview(o);setProjects(p);setRuns(r);setNodes(n);setApprovals(a);setEvents(e);setError('');
    } catch(err){setError(String(err));}
  }
  useEffect(()=>{if(!token)return;void load();const interval=setInterval(()=>{void load()},15000);return()=>clearInterval(interval)},[token]);
  async function action(path:string){
    setBusy(true);try{await request(path,{method:'POST'});await load();if(selected)await viewRun(selected)}catch(err){setError(String(err))}finally{setBusy(false)}
  }
  async function createProject(e:React.FormEvent) {
    e.preventDefault();setBusy(true);
    try{await request('/projects',{method:'POST',body:JSON.stringify({name:projectName,repo_url:projectRepo})});setShowCreate(false);setProjectName('');setProjectRepo('');await load()}catch(err){setError(String(err))}finally{setBusy(false)}
  }
  async function createRun(e:React.FormEvent) {
    e.preventDefault();setBusy(true);
    try{await request('/runs',{method:'POST',body:JSON.stringify({...newRun,tasks:JSON.parse(newRun.tasks)})});setShowRun(false);await load()}catch(err){setError(String(err))}finally{setBusy(false)}
  }
  async function viewRun(id:string){try{setDetail(await request('/runs/'+id));setSelected(id)}catch(err){setError(String(err))}}
  if(!token)return <main className="login"><div className="login-card"><div className="brand-symbol"><Layers3 size={32}/></div><h1>OHO <span>Console</span></h1><p>Orchestration Hub — secure multi-agent control plane.</p>{error&&<div className="error login-error" role="alert"><span>{error}</span><button type="button" onClick={()=>setError('')}><X size={16}/></button></div>}<form onSubmit={e=>{e.preventDefault();if(inputToken.length>=24){try{window.sessionStorage.setItem('oho-admin-token',inputToken)}catch{}setError('');setToken(inputToken);setInputToken('')}}}><label>Administrator access token</label><input type="password" placeholder="Enter your admin token" value={inputToken} onChange={e=>setInputToken(e.target.value)} autoComplete="current-password" required/><button className="primary" type="submit"><Lock size={16}/> Unlock console <ArrowRight size={16}/></button></form><small>Phiên được giữ trong tab hiện tại và vẫn tồn tại khi tải lại trang. Nhấn Lock console để đăng xuất; đóng tab sẽ kết thúc phiên.</small></div></main>;

  const nav=[['overview','Overview',Activity],['runs','Task runs',GitBranch],['nodes','Worker nodes',Server],['approvals','Approvals',ShieldCheck],['projects','Projects',Layers3],['events','Audit trail',CircleHelp]] as const;
  return <div className="shell">
    <aside className="sidebar"><div className="brand"><div className="brand-symbol"><Layers3 size={23}/></div><span>OHO <b>Control</b></span></div><div className="side-label">WORKSPACE</div>
      <nav>{nav.map(([key,label,Icon])=><button key={key} className={active===key?'selected':''} onClick={()=>{setActive(key);setSelected(null);setDetail(null)}}><Icon size={18}/>{label}{key==='approvals'&&overview.pending_approvals>0&&<span className="count">{overview.pending_approvals}</span>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="service"><span className="online-dot"/> Control plane connected</div><button onClick={()=>{try{window.sessionStorage.removeItem('oho-admin-token')}catch{}setToken('');setInputToken('');setError('');setDetail(null)}}><Lock size={15}/> Lock console</button></div>
    </aside>
    <div className="main"><header className="topbar"><span>OHO / {nav.find(n=>n[0]===active)?.[1]}</span><div><span className="live">● Live</span><button title="Refresh" className="icon-button" onClick={()=>void load()}><RefreshCw size={17}/></button></div></header>
      <section className="content">
      {error&&<div className="error"><span>{error}</span><button onClick={()=>setError('')}><X size={16}/></button></div>}
      <div className="headline"><div><div className="eyebrow">OPERATIONS CENTER</div><h1>{nav.find(n=>n[0]===active)?.[1]}</h1><p>Manage distributed coding tasks with human approval gates.</p></div><div className="headline-actions">{active==='projects'&&<button className="primary" onClick={()=>setShowCreate(!showCreate)}>+ Add project</button>}{active==='runs'&&<button className="primary" onClick={()=>setShowRun(!showRun)}>+ Create run</button>}</div></div>
      {active==='overview'&&<><div className="stats">
        {([['Projects',overview.projects,Layers3],['Runs',overview.runs,GitBranch],['Connected nodes',nodes.filter(n=>n.online).length,Server],['Pending approvals',overview.pending_approvals,ShieldCheck]] as const).map(([label,value,Icon])=><div className="stat" key={String(label)}><div className="stat-title">{String(label)} <Icon size={19}/></div><div className="stat-value">{String(value)}</div></div>)}
        </div><div className="panels"><div className="panel"><h3>Recent runs</h3>{runs.slice(0,6).map(r=><div className="record" key={r.id}><div><strong>{r.title}</strong><small>{r.id.slice(0,8)}</small></div><Status text={r.status}/></div>)}{!runs.length&&<Empty/>}</div><div className="panel"><h3>Worker fleet</h3>{nodes.map(n=><div className="record" key={n.id}><div><strong>{n.name}</strong><small>{n.platform} • {n.models.join(', ')||'No adapters'}</small></div><Status text={n.online?'online':'offline'}/></div>)}{!nodes.length&&<Empty/>}</div></div></>}
      {active==='nodes'&&<div className="panel"><h3>Registered worker nodes <span className="subtle">({nodes.length})</span></h3><div className="table-scroll"><table><thead><tr><th>Node</th><th>Platform</th><th>Models</th><th>Tags</th><th>Health</th></tr></thead><tbody>{nodes.map(n=><tr key={n.id}><td><b>{n.name}</b></td><td>{n.platform}</td><td>{n.models.join(', ')||'—'}</td><td>{n.tags.join(', ')||'—'}</td><td><Status text={n.online?'online':'offline'}/></td></tr>)}</tbody></table></div>{!nodes.length&&<Empty/>}</div>}
      {active==='runs'&&<><div className="panel"><h3>Workflow runs</h3>{runs.map(r=><button className="record record-button" key={r.id} onClick={()=>void viewRun(r.id)}><div><strong>{r.title}</strong><small>{new Date(r.created_at).toLocaleString()} • {r.id.slice(0,8)}</small></div><Status text={r.status}/></button>)}{!runs.length&&<Empty/>}</div>{detail&&<div className="panel"><h3>{detail.run.title} — DAG tasks</h3>{detail.tasks.map(t=><div className="task" key={t.id}><div className="task-meta"><b>{t.key}</b><Status text={t.status}/></div><small>Kind: {t.kind} • Risk: {t.risk} • Dependencies: {t.depends_on.join(', ')||'none'} • Attempts: {t.attempts}</small>{t.output&&<pre>{t.output}</pre>}</div>)}</div>}</>}
      {active==='approvals'&&<div className="panel"><h3>Tasks requiring explicit human approval</h3>{approvals.map(t=><div className="approval" key={t.id}><div><strong>{t.key}</strong><small>Risk: {t.risk} • Run: {t.run_id.slice(0,8)} • {t.kind}</small></div><div className="buttons"><button disabled={busy} onClick={()=>void action('/tasks/'+t.id+'/reject')}><X size={15}/> Reject</button><button className="primary" disabled={busy} onClick={()=>void action('/tasks/'+t.id+'/approve')}><Check size={15}/> Approve</button></div></div>)}{!approvals.length&&<Empty text="No approvals waiting."/ >}</div>}
      {active==='projects'&&<div className="panel"><h3>Git repositories</h3>{projects.map(p=><div className="record" key={p.id}><div><strong>{p.name}</strong><small>{p.repo_url}</small></div><GitBranch size={18}/></div>)}{!projects.length&&<Empty/>}</div>}
      {active==='events'&&<div className="panel"><h3>Audit activity</h3>{events.map(e=><div className="record" key={e.id}><div><strong>{e.kind}</strong><small>{e.detail} • {new Date(e.created_at).toLocaleString()}</small></div></div>)}{!events.length&&<Empty/>}</div>}
      {showCreate&&<div className="modal-backdrop"><form className="modal" onSubmit={createProject}><h3>Add GitHub project</h3><label>Project name</label><input required minLength={2} value={projectName} onChange={e=>setProjectName(e.target.value)} placeholder="my-project"/><label>GitHub HTTPS URL</label><input required value={projectRepo} onChange={e=>setProjectRepo(e.target.value)} placeholder="https://github.com/owner/repo"/><div className="buttons"><button type="button" onClick={()=>setShowCreate(false)}>Cancel</button><button className="primary" disabled={busy}>Create</button></div></form></div>}
      {showRun&&<div className="modal-backdrop"><form className="modal" onSubmit={createRun}><h3>Create task DAG</h3><label>Project</label><select required value={newRun.project_id} onChange={e=>setNewRun({...newRun,project_id:e.target.value})}><option value="">Choose project</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select><label>Run title</label><input required value={newRun.title} onChange={e=>setNewRun({...newRun,title:e.target.value})}/><label>Tasks (JSON array)</label><textarea rows={9} value={newRun.tasks} onChange={e=>setNewRun({...newRun,tasks:e.target.value})}/><div className="buttons"><button type="button" onClick={()=>setShowRun(false)}>Cancel</button><button className="primary" disabled={busy}>Queue run</button></div></form></div>}
      </section><footer>OHO v0.1 • Operator review required for high-risk actions</footer>
    </div></div>;
}
function Status({text}:{text:string}){return <span className={'status '+(text==='online'||text==='succeeded'?'good':text==='failed'||text==='rejected'?'bad':text==='awaiting_approval'?'warn':'')}>{text.replaceAll('_',' ')}</span>}
function Empty({text='No records yet.'}:{text?:string}){return <div className="empty"><Cpu size={26}/><p>{text}</p></div>}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
