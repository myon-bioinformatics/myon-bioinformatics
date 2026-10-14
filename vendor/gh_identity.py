#!/usr/bin/env python3
"""gh_identity: stdlib-only GitHub operations with gh-first/urllib fallback."""
from __future__ import annotations
from collections import Counter
import base64,io,zipfile
import argparse,fnmatch,json,os,re,shutil,signal,subprocess,sys,urllib.error,urllib.parse,urllib.request
from datetime import datetime,timezone
from html.parser import HTMLParser
import hashlib
import http.client
API="https://api.github.com"; REPO=re.compile(r"^[\w.-]+/[\w.-]+$")
class Error(RuntimeError):
 def __init__(self,code,uncertain=False): super().__init__(code); self.code=code; self.uncertain=uncertain
import contextlib
import contextvars
import functools
import inspect
import math
import threading
import time
import tempfile


class _Budget:
 def __init__(self,max_pages=100,max_items=10000,max_bytes=10000000,timeout=30):
  for value in (max_pages,max_items,max_bytes):
   if type(value) is not int or value<1:raise ValueError("limits must be positive integers")
  if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<=0:
   raise ValueError("timeout must be positive and finite")
  self.max_pages=max_pages;self.max_items=max_items;self.max_bytes=max_bytes
  self.failure=None;self.pages=0;self.items=0;self.bytes=0;self.deadline=time.monotonic()+timeout
 def fail(self,code,uncertain=False):
  if self.failure is None:self.failure=code
  raise Error(self.failure,uncertain)
 def remaining(self):
  if self.failure is not None:raise Error(self.failure)
  left=self.deadline-time.monotonic()
  if left<=0:self.fail("operation_timeout")
  return left
 def charge(self,kind,count):
  self.remaining()
  value=getattr(self,kind)+count
  if value>getattr(self,"max_"+kind):self.fail(kind+"_limit")
  setattr(self,kind,value)

_BUDGET=contextvars.ContextVar("ghi_operation_budget",default=None)

@contextlib.contextmanager
def operation(*,max_pages=100,max_items=10000,max_bytes=10000000,timeout=30):
 """Set cumulative limits for a group of calls; nested scopes cannot reset them."""
 candidate=_Budget(max_pages,max_items,max_bytes,timeout)
 current=_BUDGET.get()
 if current is not None:
  raise ValueError("operation scopes cannot be nested")
 token=_BUDGET.set(candidate)
 try:yield
 finally:_BUDGET.reset(token)

def _bounded(fn):
 signature=inspect.signature(fn)
 @functools.wraps(fn)
 def call(*args,**kwargs):
  bound=signature.bind(*args,**kwargs)
  timeout=bound.arguments.get("timeout",bound.arguments.get("k",{}).get("timeout",30))
  if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<=0:
   raise ValueError("timeout must be positive and finite")
  if _BUDGET.get() is not None:
   _BUDGET.get().remaining()
   return fn(*args,**kwargs)
  with operation(timeout=timeout):return fn(*args,**kwargs)
 return call

def _process(argv,payload,timeout,env=None,mutating=False):
 """Bound both pipes and wall time; never use communicate/capture_output."""
 budget=_BUDGET.get();left=min(timeout,budget.remaining())
 cap=budget.max_bytes-budget.bytes
 if cap<=0:budget.fail("bytes_limit")
 try:p=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
 except FileNotFoundError as e:raise Error("process_not_found") from e
 except OSError as e:raise Error("process_error") from e
 buffers=[bytearray(),bytearray()];errors=[];lock=threading.Lock();wake=threading.Event()
 def drain(stream,index):
  try:
   while True:
    chunk=stream.read1(min(65536,cap+1))
    if not chunk:break
    with lock:
     available=cap-sum(map(len,buffers))
     buffers[index].extend(chunk[:available])
     if len(chunk)>available:
      errors.append("bytes_limit");wake.set();return
  except (OSError,ValueError):
   with lock:errors.append("process_error")
  finally:stream.close();wake.set()
 def write():
  try:
   if payload:p.stdin.write(payload)
  except (BrokenPipeError,OSError):pass
  finally:
   try:p.stdin.close()
   except OSError:pass
   wake.set()
 threads=[threading.Thread(target=drain,args=(p.stdout,0),daemon=True),
          threading.Thread(target=drain,args=(p.stderr,1),daemon=True),
          threading.Thread(target=write,daemon=True)]
 for thread in threads:thread.start()
 deadline=min(budget.deadline,time.monotonic()+left)
 failure=None
 try:
  while p.poll() is None or any(t.is_alive() for t in threads):
   if errors:failure=errors[0];break
   left=deadline-time.monotonic()
   if left<=0:failure="operation_timeout";break
   wake.wait(min(left,.02));wake.clear()
  if not failure and errors:failure=errors[0]
 finally:
  if p.poll() is None:p.kill()
  p.wait()
 if failure in ("bytes_limit","operation_timeout"):budget.fail(failure,mutating)
 if failure:raise Error(failure,mutating)
 raw,err=map(bytes,buffers)
 try:budget.charge("bytes",len(raw)+len(err))
 except Error as e:raise Error(e.code,mutating) from e
 return p.returncode,raw,err

_HTTP_CODES={401:3,403:4,404:5,422:6}
_HTTP_ERRORS={3:"authentication_required",4:"permission_or_rate_limit",5:"not_found_or_inaccessible",
              6:"rejected",7:"http_error",8:"transport_error",9:"bytes_limit",10:"server_error"}

def _http_worker():
 """Private isolated urllib worker, killed by the parent on deadline/overflow."""
 spec=json.load(sys.stdin)
 data=None if spec["payload"] is None else json.dumps(spec["payload"]).encode()
 req=urllib.request.Request(spec["url"],data=data,headers=spec["headers"],method=spec["method"])
 try:
  with urllib.request.urlopen(req,timeout=spec["timeout"]) as response:
   # At most cap+1 bytes are ever retained by this worker.
   raw=response.read(spec["cap"]+1)
   if len(raw)>spec["cap"]:return 9
   sys.stdout.buffer.write(raw)
 except urllib.error.HTTPError as e:
  e.close()
  return _HTTP_CODES.get(e.code,10 if e.code>=500 else 7)
 except (urllib.error.URLError,TimeoutError,OSError):return 8
 return 0

class Page:
 """Optional injected response carrying data and HTTP Link headers."""
 def __init__(self,data,headers=None):self.data=data;self.headers=headers or {}

def _page(path,transport,timeout,requester=None):
 budget=_BUDGET.get();budget.charge("pages",1)
 result=(requester or request)("GET",path,transport=transport,timeout=min(timeout,budget.remaining()))
 budget.remaining()
 if requester is not None:
  data=result.data if isinstance(result,Page) else result
  budget.charge("bytes",len(json.dumps(data).encode()))
 if isinstance(result,Page):
  if not isinstance(result.headers,dict):raise Error("invalid_pagination_link")
  return result.data,result.headers
 return result,None

def _next_page(path,headers,count):
 link=None if headers is None else next((v for k,v in headers.items() if isinstance(k,str) and k.lower()=="link"),"")
 if link is not None:
  if not isinstance(link,str):raise Error("invalid_pagination_link")
  candidates=re.findall(r'<([^>]+)>\s*;\s*rel=["\']?next["\']?',link)
  if len(candidates)>1:raise Error("invalid_pagination_link")
  if not candidates:return None
  target=urllib.parse.urlsplit(urllib.parse.urljoin(API+"/"+path,candidates[0]))
  before=urllib.parse.urlsplit(API+"/"+path)
  old=urllib.parse.parse_qs(before.query);new=urllib.parse.parse_qs(target.query)
  try:valid_page=new.pop("page")==[str(int(old.pop("page")[0])+1)]
  except (KeyError,ValueError,IndexError):raise Error("invalid_pagination_link")
  if target.scheme!="https" or target.netloc!="api.github.com" or target.path!=before.path or target.fragment or new!=old or not valid_page:
   raise Error("invalid_pagination_link")
  return target.path.lstrip("/")+"?"+target.query
 if count<100:return None
 parsed=urllib.parse.urlsplit(path);query=urllib.parse.parse_qs(parsed.query)
 query["page"]=[str(int(query["page"][0])+1)]
 return parsed.path+"?"+urllib.parse.urlencode(query,doseq=True)

def now(): return datetime.now(timezone.utc).isoformat()
def repo(x):
 if not isinstance(x,str) or not REPO.fullmatch(x): raise ValueError("repo must be OWNER/REPO")
 return x
def token(): return os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
def gh_available(): return shutil.which("gh") is not None
def _gh(method,path,payload=None,timeout=30,mutating=False):
 env=os.environ.copy(); env.update(GH_PROMPT_DISABLED="1",GH_PAGER="cat"); env.pop("GH_REPO",None)
 a=["gh","api","--method",method,path]+(["--input","-"] if payload is not None else [])
 try:code,raw,err=_process(a,None if payload is None else json.dumps(payload).encode(),timeout,env,mutating)
 except Error as e:
  if e.code=="process_not_found":raise Error("gh_not_found") from e
  raise
 if code:
  message=(err or raw).decode("utf-8",errors="replace").lower()
  c="authentication_required" if code==4 else "cancelled" if code==2 else "gh_failed"
  if "http 401" in message:c="authentication_required"
  if "http 403" in message:c="permission_or_rate_limit"
  if "http 404" in message:c="not_found_or_inaccessible"
  raise Error(c,mutating and ("http 5" in message or "timed out" in message))
 if not raw.strip():return None
 try:return json.loads(raw.decode("utf-8"))
 except (UnicodeError,json.JSONDecodeError) as e:raise Error("invalid_json",mutating) from e

def _url(method,path,payload=None,timeout=30,mutating=False):
 headers={"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","User-Agent":"gh_identity/0.1"}
 if token():headers["Authorization"]="Bearer "+token()
 budget=_BUDGET.get()
 spec={"url":API+"/"+path.lstrip("/"),"method":method,"payload":payload,"headers":headers,
       "timeout":min(timeout,budget.remaining()),"cap":budget.max_bytes-budget.bytes}
 # Credentials go over stdin, never command-line arguments or diagnostics.
 code,raw,err=_process([sys.executable,os.path.abspath(__file__),"--_http-worker"],json.dumps(spec).encode(),timeout,mutating=mutating)
 if code==9:budget.fail("bytes_limit",mutating)
 if code:raise Error(_HTTP_ERRORS.get(code,"transport_error"),mutating and code not in (3,4,5,6))
 if not raw:return None
 try:return json.loads(raw.decode("utf-8"))
 except (UnicodeError,json.JSONDecodeError) as e:raise Error("invalid_json",mutating) from e

def request(method,path,payload=None,transport="auto",timeout=30,mutating=False):
 stub=_REQUEST.get()
 if stub is not None:return stub(method,path,payload=payload,transport=transport,timeout=timeout,mutating=mutating)
 if transport not in ("auto","gh","urllib"):raise ValueError("bad transport")
 if transport=="gh" or (transport=="auto" and gh_available()):
  try:return _gh(method,path,payload,timeout,mutating)
  except Error as e:
   if transport=="gh" or e.code not in ("gh_not_found","authentication_required"):raise
 return _url(method,path,payload,timeout,mutating)

def pages(path,transport="auto",timeout=30,*,requester=None):
 out=[];sep="&" if "?" in path else "?";path+=sep+"per_page=100&page=1"
 while path:
  data,headers=_page(path,transport,timeout,requester)
  if not isinstance(data,list):raise Error("invalid_json")
  _BUDGET.get().charge("items",len(data));out.extend(data)
  path=_next_page(path,headers,len(data))
 return out
def pages_bounded(path,transport="auto",timeout=30,*,max_pages=20,requester=None):
 """Like pages() but stops after max_pages; returns (items, complete, pages_fetched)."""
 if type(max_pages) is not int or max_pages<1:raise ValueError("max_pages must be positive")
 out=[];n=0;sep="&" if "?" in path else "?";path+=sep+"per_page=100&page=1"
 while path:
  if n>=max_pages:return out,False,n
  data,headers=_page(path,transport,timeout,requester);n+=1
  if not isinstance(data,list):raise Error("invalid_json")
  b=_BUDGET.get()
  if b is not None:b.charge("items",len(data))
  out.extend(data);path=_next_page(path,headers,len(data))
 return out,True,n
def capabilities():
 versions=tool_versions(("git","gh"))
 return {"schema":"gh-identity-capabilities/1","observed_at":now(),"python":sys.version.split()[0],
         "gh":gh_available(),"git":shutil.which("git") is not None,"token_present":bool(token()),
         "git_version":versions.get("git"),"gh_version":versions.get("gh")}
def repository(r,**k):
 r=repo(r);d=request("GET",f"repos/{r}",**k)
 return {"schema":"gh-identity-repository/1","repository":r,"id":d.get("id"),"default_branch":d.get("default_branch"),"visibility":d.get("visibility"),"archived":d.get("archived"),"url":d.get("html_url"),"observed_at":now()}
def repositories(owner,transport="auto",timeout=30):
 xs=pages(f"users/{owner}/repos?sort=full_name",transport,timeout)
 rows=[{"full_name":x.get("full_name"),"archived":x.get("archived"),"updated_at":x.get("updated_at"),"url":x.get("html_url")} for x in xs]
 return {"schema":"gh-identity-repositories/1","owner":owner,"complete":True,"count":len(rows),"repositories":rows}
def repository_inventory(owner, *, fields=("name",), sort="name", order="asc",
                         max_repos=30, max_pages=5, transport="auto", timeout=30):
 """Bounded public non-archived repository inventory; run counts are opt-in."""
 if not isinstance(owner,str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37})",owner):
  raise ValueError("invalid owner")
 allowed={"name","size-kb","run-count","updated-at"}
 if isinstance(fields,str):fields=tuple(x.strip() for x in fields.split(","))
 else:fields=tuple(fields)
 if not fields or any(x not in allowed for x in fields):raise ValueError("invalid fields")
 if sort not in allowed or order not in ("asc","desc"):raise ValueError("invalid sort/order")
 if type(max_repos) is not int or not 1<=max_repos<=1000:raise ValueError("invalid max_repos")
 if type(max_pages) is not int or not 1<=max_pages<=100:raise ValueError("invalid max_pages")
 need_counts="run-count" in fields or sort=="run-count"
 rows=[];page=1;exhausted=False;pages_fetched=0
 while page<=max_pages and len(rows)<max_repos:
  data,_headers=_page("users/"+owner+"/repos?per_page=100&page="+str(page),
                      transport=transport,timeout=timeout)
  if not isinstance(data,list) or len(data)>100:raise Error("invalid_json")
  _BUDGET.get().charge("items",len(data))
  pages_fetched+=1
  for item in data:
   if not isinstance(item,dict):raise Error("invalid_json")
   if item.get("private") is not False or item.get("archived") is not False:continue
   name=item.get("name");full=item.get("full_name")
   if not isinstance(name,str) or full!=owner+"/"+name:raise Error("invalid_repository_identity")
   size=item.get("size")
   if type(size) is not int or size<0:size=None
   row={"name":name,"full_name":full,"size_kb":size,"updated_at":item.get("updated_at"),
        "run_count":None,"run_count_status":"not_requested"}
   rows.append(row)
   if len(rows)>=max_repos:break
  if len(data)<100 and len(rows)<max_repos:
   exhausted=True
   break
  page+=1
 for row in rows:
  if not need_counts:continue
  try:
   result=request("GET","repos/"+row["full_name"]+"/actions/runs?per_page=1",
                  transport=transport,timeout=timeout)
   count=result.get("total_count") if isinstance(result,dict) else None
   if type(count) is not int or count<0:raise Error("invalid_json")
   row["run_count"]=count;row["run_count_status"]="available"
  except Error as exc:
   if exc.code in ("authentication_required","permission_or_rate_limit","not_found_or_inaccessible"):
    row["run_count_status"]=exc.code
   else:raise
 keymap={"name":"name","size-kb":"size_kb","run-count":"run_count","updated-at":"updated_at"}
 key=keymap[sort]
 # Normalize timestamps to UTC before ordering; malformed values sort as missing.
 def sort_value(row):
  value=row[key]
  if sort=="updated-at":
   if not isinstance(value,str):return None
   try:
    parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
    if parsed.tzinfo is None:return None
    return parsed.astimezone(timezone.utc)
   except (ValueError,OverflowError):return None
  return value
 decorated=[(sort_value(row),row) for row in rows]
 known=[(value,row) for value,row in decorated if value is not None]
 missing=[row for value,row in decorated if value is None]
 # Stable secondary name order, even for equal metric values.
 known.sort(key=lambda pair:pair[1]["name"])
 known.sort(key=lambda pair:pair[0],reverse=order=="desc")
 missing.sort(key=lambda row:row["name"])
 rows=[row for _,row in known]+missing
 return {"schema":"gh-identity-repository-inventory/1","owner":owner,"repositories":rows,
         "count":len(rows),"complete":exhausted,"truncated":not exhausted,
         "limit_reason":None if exhausted else ("max_repos" if len(rows)>=max_repos else "max_pages"),
         "pages_fetched":pages_fetched,"sort":sort,"order":order,
         "sort_scope":"complete" if exhausted else "partial",
         "fields":list(fields),"measured_at":now()}

def pr(r,n,**k):
 r=repo(r);d=request("GET",f"repos/{r}/pulls/{n}",**k)
 return {"schema":"gh-identity-pr/1","repository":r,"number":n,"state":d.get("state"),"draft":d.get("draft"),"merged":d.get("merged"),"mergeable":d.get("mergeable"),"mergeable_state":d.get("mergeable_state"),"head_sha":(d.get("head")or{}).get("sha"),"head_ref":(d.get("head")or{}).get("ref"),"base_sha":(d.get("base")or{}).get("sha"),"base_ref":(d.get("base")or{}).get("ref"),"url":d.get("html_url"),"observed_at":now()}
def _review_limits(n,max_items,max_pages):
 n=_positive_identifier(n,"PR number")
 for value in (max_items,max_pages):
  if type(value) is not int or value<1:raise ValueError("limits must be positive integers")
 return n

def _review_rest(path,transport,timeout,max_items,max_pages):
 if max_items is None and max_pages is None:
  return pages(path,transport,timeout),True,None
 max_items=100 if max_items is None else max_items
 max_pages=10 if max_pages is None else max_pages
 _review_limits(1,max_items,max_pages)
 rows=[];seen=set();path+="?per_page=100&page=1"
 for _ in range(max_pages):
  batch,headers=_page(path,transport,timeout)
  if not isinstance(batch,list) or any(not isinstance(x,dict) for x in batch):raise Error("invalid_json")
  _BUDGET.get().charge("items",len(batch))
  for x in batch:
   if x.get("body") is not None and not isinstance(x["body"],str):raise Error("invalid_json")
   if x.get("user") is not None and not isinstance(x["user"],dict):raise Error("invalid_json")
   identity=x.get("id")
   if type(identity) is not int or identity<1 or identity in seen:raise Error("ambiguous_comment_identity")
   seen.add(identity)
  remaining=max_items-len(rows);rows.extend(batch[:remaining])
  next_path=_next_page(path,headers,len(batch))
  if len(batch)>remaining:return rows,False,"max_items"
  if next_path is None:return rows,True,None
  if len(rows)>=max_items:return rows,False,"max_items"
  path=next_path
 return rows,False,"max_pages"

def _explicit_severity(body):
 # Only standalone labels or labels introducing a finding; never prose keywords.
 labels=[]
 pattern=r"^\s*(?:[-*]\s+|#{1,6}\s+)?(?:\*\*)?(Blocking|Should|Non-blocking|Nit)(?:\*\*)?(?=\s*(?::|$))"
 fenced=False
 for line in body.splitlines():
  if re.match(r"^\s*(```|~~~)",line):fenced=not fenced;continue
  if fenced:continue
  match=re.match(pattern,line,re.I)
  if match:
   label={"blocking":"Blocking","should":"Should","non-blocking":"Non-blocking","nit":"Nit"}[match[1].lower()]
   labels.append({"label":label,"source":"explicit_body_label","text":line})
 return labels

def _review_body(x):
 body=x.get("body") or ""
 if not isinstance(body,str):raise Error("invalid_json")
 return {"preview":re.sub(r"\s+"," ",body)[:240],"body_sha256":hashlib.sha256(body.encode()).hexdigest(),
         "severity_labels":_explicit_severity(body)}

def comments(r,n,last=None,transport="auto",timeout=30,*,max_items=None,max_pages=None):
 r=repo(r)
 if last is not None and (type(last) is not int or last<0):raise ValueError("last must be a nonnegative integer")
 xs,complete,reason=_review_rest(f"repos/{r}/issues/{n}/comments",transport,timeout,max_items,max_pages)
 ds=[dict({"id":x.get("id"),"kind":"conversation","author":(x.get("user")or{}).get("login"),"created_at":x.get("created_at"),"chars":len(x.get("body")or""),"preview":re.sub(r"\s+"," ",x.get("body")or"")[:240],"url":x.get("html_url")},**_review_body(x)) for x in xs]
 if last is not None:ds=ds[-last:] if last else []
 return {"schema":"gh-identity-comments/1","repository":r,"number":n,"total":len(xs),"shown":len(ds),"complete":complete,"truncated":not complete,"limit_reason":reason,"comments":ds}
def reviews(r,n,transport="auto",timeout=30,*,max_items=None,max_pages=None):
 r=repo(r);xs,complete,reason=_review_rest(f"repos/{r}/pulls/{n}/reviews",transport,timeout,max_items,max_pages)
 ds=[dict({"id":x.get("id"),"kind":"review_submission","author":(x.get("user")or{}).get("login"),"state":x.get("state"),"commit_id":x.get("commit_id"),"submitted_at":x.get("submitted_at"),"chars":len(x.get("body")or""),"url":x.get("html_url")},**_review_body(x)) for x in xs]
 return {"schema":"gh-identity-reviews/1","repository":r,"number":n,"count":len(ds),"complete":complete,"truncated":not complete,"limit_reason":reason,"reviews":ds}

def inline_comments(r,n,*,max_items=100,max_pages=10,transport="auto",timeout=30):
 r=repo(r);n=_review_limits(n,max_items,max_pages)
 xs,complete,reason=_review_rest(f"repos/{r}/pulls/{n}/comments",transport,timeout,max_items,max_pages)
 fields=("id","node_id","pull_request_review_id","in_reply_to_id","path","diff_hunk","line","original_line","start_line","original_start_line","side","start_side","position","original_position","commit_id","original_commit_id","created_at","updated_at","author_association")
 rows=[dict({key:x.get(key) for key in fields},kind="inline_comment",url=x.get("html_url"),author=(x.get("user")or{}).get("login"),**_review_body(x)) for x in xs]
 return {"schema":"gh-identity-inline-comments/1","repository":r,"number":n,"comments":rows,"complete":complete,"truncated":not complete,"limit_reason":reason}

_THREAD_FIELDS="""id isResolved isOutdated path line originalLine startLine originalStartLine diffSide startDiffSide resolvedBy { login }
 comments(first:100) { nodes { databaseId url } totalCount pageInfo { hasNextPage endCursor } }"""

def _review_graphql(query,variables,transport,timeout):
 budget=_BUDGET.get();budget.charge("pages",1)
 # POST is GraphQL query transport, not a mutation. Both gh and urllib supported.
 data=request("POST","graphql",{"query":query,"variables":variables},transport=transport,timeout=min(timeout,budget.remaining()))
 if not isinstance(data,dict):raise Error("invalid_json")
 if data.get("errors"):
  errors=data["errors"]
  if not isinstance(errors,list):raise Error("invalid_json")
  types={x.get("type") for x in errors if isinstance(x,dict)}
  raise Error("permission_or_rate_limit" if types & {"FORBIDDEN","RATE_LIMITED"} else "graphql_error")
 if not isinstance(data.get("data"),dict):raise Error("invalid_json")
 return data["data"]

def _review_connection(connection):
 if not isinstance(connection,dict) or not isinstance(connection.get("nodes"),list):raise Error("invalid_json")
 info=connection.get("pageInfo")
 if not isinstance(info,dict) or type(info.get("hasNextPage")) is not bool:raise Error("pagination_incomplete")
 cursor=info.get("endCursor")
 if info["hasNextPage"] and (not isinstance(cursor,str) or not cursor):raise Error("pagination_incomplete")
 if any(not isinstance(x,dict) for x in connection["nodes"]):raise Error("invalid_json")
 _BUDGET.get().charge("items",len(connection["nodes"]))
 return connection["nodes"],cursor if info["hasNextPage"] else None

def review_threads(r,n,*,max_items=100,max_pages=10,transport="auto",timeout=30):
 r=repo(r);n=_review_limits(n,max_items,max_pages);owner,name=r.split("/")
 query="query($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){nodes{"+_THREAD_FIELDS+"} pageInfo{hasNextPage endCursor}}}}}"
 rows=[];cursor=None;seen=set();cursors=set();reason=None
 for _ in range(max_pages):
  data=_review_graphql(query,dict(owner=owner,name=name,number=n,cursor=cursor),transport,timeout)
  repository=data.get("repository")
  pull=repository.get("pullRequest") if isinstance(repository,dict) else None
  if not isinstance(pull,dict):raise Error("not_found_or_inaccessible")
  nodes,next_cursor=_review_connection(pull.get("reviewThreads"))
  for x in nodes:
   if len(rows)>=max_items:reason="max_items";break
   identity=x.get("id")
   if not isinstance(identity,str) or not identity or identity in seen:raise Error("ambiguous_thread_identity")
   seen.add(identity)
   if type(x.get("isResolved")) is not bool or type(x.get("isOutdated")) is not bool:raise Error("invalid_json")
   members,member_cursor=_review_connection(x.get("comments"));members=list(members)
   member_reason=None;member_seen=set();member_pages=1
   while member_cursor and len(members)<max_items and member_pages<max_pages:
    if member_cursor in member_seen:raise Error("pagination_incomplete")
    member_seen.add(member_cursor)
    q="query($id:ID!,$cursor:String){node(id:$id){... on PullRequestReviewThread{comments(first:100,after:$cursor){nodes{databaseId url} totalCount pageInfo{hasNextPage endCursor}}}}}"
    d=_review_graphql(q,{"id":identity,"cursor":member_cursor},transport,timeout)
    node=d.get("node")
    if not isinstance(node,dict):raise Error("not_found_or_inaccessible")
    more,member_cursor=_review_connection(node.get("comments"));members.extend(more);member_pages+=1
   if len(members)>max_items or member_cursor:member_reason="max_items" if len(members)>=max_items else "max_pages"
   members=members[:max_items]
   ids=[c.get("databaseId") for c in members]
   if any(type(i) is not int or i<1 for i in ids) or len(set(ids))!=len(ids):raise Error("ambiguous_comment_identity")
   total=x["comments"].get("totalCount")
   if type(total) is not int or total<0 or (not member_reason and total!=len(members)):raise Error("pagination_incomplete")
   if x.get("resolvedBy") is not None and not isinstance(x["resolvedBy"],dict):raise Error("invalid_json")
   row={"kind":"review_thread","id":identity,"is_resolved":x["isResolved"],"is_outdated":x["isOutdated"],"resolved_by":(x.get("resolvedBy") or {}).get("login"),"comment_ids":ids,"url":members[0].get("url") if members else None,"comments_complete":member_reason is None,"comments_limit_reason":member_reason}
   for key,source in (("path","path"),("line","line"),("original_line","originalLine"),("start_line","startLine"),("original_start_line","originalStartLine"),("side","diffSide"),("start_side","startDiffSide")):row[key]=x.get(source)
   rows.append(row)
  if reason:break
  if next_cursor is None:break
  if len(rows)>=max_items:reason="max_items";break
  if next_cursor in cursors:raise Error("pagination_incomplete")
  cursors.add(next_cursor);cursor=next_cursor
 else:reason="max_pages"
 complete=reason is None and all(x["comments_complete"] for x in rows)
 return {"schema":"gh-identity-review-threads/1","repository":r,"number":n,"threads":rows,"complete":complete,"truncated":not complete,"limit_reason":reason or ("thread_comments" if not complete else None)}

def review_observations(r,n,*,max_items=100,max_pages=10,transport="auto",timeout=30):
 r=repo(r);n=_review_limits(n,max_items,max_pages)
 result={"schema":"gh-identity-review-observations/1","repository":r,"number":n,"observed_at":now(),"components":{}}
 for key,fn,field in (("conversation",comments,"comments"),("review_submission",reviews,"reviews"),("inline_comment",inline_comments,"comments"),("review_thread",review_threads,"threads")):
  try:
   value=fn(r,n,max_items=max_items,max_pages=max_pages,transport=transport,timeout=timeout)
   result[key]=value[field]
   result["components"][key]={k:value.get(k) for k in ("complete","truncated","limit_reason")}
  except Error as e:
   if e.code in {"pages_limit","items_limit","bytes_limit","operation_timeout"}:raise
   result[key]=[];result["components"][key]={"complete":False,"truncated":False,"error":e.code}
 result["complete"]=all(x["complete"] for x in result["components"].values())
 result["truncated"]=any(x["truncated"] for x in result["components"].values())
 # Link only observed identities; a missing mapping is unknown, never resolved.
 mapping={}
 for thread in result["review_thread"]:
  for identity in thread["comment_ids"]:
   if identity in mapping:raise Error("ambiguous_comment_identity")
   mapping[identity]=thread["id"]
 for x in result["inline_comment"]:x["thread_id"]=mapping.get(x["id"])
 return result

def diff_pr_observations(before,after):
 """Pure identity-based comparison; partial input cannot establish removal."""
 for value in (before,after):
  if not isinstance(value,dict) or value.get("schema")!="gh-identity-review-observations/1":raise ValueError("review observation required")
 if (before["repository"],before["number"])!=(after["repository"],after["number"]):raise ValueError("PR identity mismatch")
 changes={}
 for kind in ("conversation","review_submission","inline_comment","review_thread"):
  def index(value):
   rows=value[kind];out={x["id"]:x for x in rows}
   if len(out)!=len(rows):raise ValueError("duplicate observation identity")
   return out
  old,new=index(before),index(after)
  complete_before=before["components"][kind]["complete"]
  complete_after=after["components"][kind]["complete"]
  changes[kind]={"added":sorted(new.keys()-old.keys(),key=str) if complete_before else [],
                 "removed":sorted(old.keys()-new.keys(),key=str) if complete_after else [],
                 "changed":[key for key in sorted(old.keys() & new.keys(),key=str) if old[key]!=new[key]],
                 "complete":complete_before and complete_after}
 return {"schema":"gh-identity-review-diff/1","repository":after["repository"],"number":after["number"],"changes":changes,"complete":before["complete"] and after["complete"]}

def pull_requests(r,state="open",max_items=100,max_pages=10,transport="auto",timeout=30):
 r=repo(r)
 if state not in {"open","closed","all"}:raise ValueError("invalid state")
 if not isinstance(max_items,int) or max_items<1:raise ValueError("max_items must be positive")
 if not isinstance(max_pages,int) or max_pages<1:raise ValueError("max_pages must be positive")
 rows=[];page=1;exhausted=False;pages_fetched=0
 while page<=max_pages and len(rows)<max_items:
  batch,_headers=_page(f"repos/{r}/pulls?state={state}&sort=updated&direction=desc&per_page=100&page={page}",transport=transport,timeout=timeout)
  pages_fetched += 1
  if not isinstance(batch,list):raise Error("invalid_json")
  _BUDGET.get().charge("items",len(batch))
  consumed=0
  for x in batch:
   consumed+=1
   rows.append({"number":x.get("number"),"title":x.get("title"),"state":x.get("state"),"draft":bool(x.get("draft")),
    "head_sha":(x.get("head")or{}).get("sha"),"head_ref":(x.get("head")or{}).get("ref"),
    "base_sha":(x.get("base")or{}).get("sha"),"base_ref":(x.get("base")or{}).get("ref"),
    "created_at":x.get("created_at"),"updated_at":x.get("updated_at"),"closed_at":x.get("closed_at"),"merged_at":x.get("merged_at"),"url":x.get("html_url")})
   if len(rows)>=max_items:break
  if len(batch)<100:exhausted=consumed==len(batch);break
  page+=1
 return {"schema":"gh-identity-pr-discovery/1","repository":r,"state":state,"pull_requests":rows,
  "count":len(rows),"pages_fetched":pages_fetched,"complete":exhausted,"truncated":not exhausted}


def _issue_row(d,expected_repo=None,expected_number=None,kind=None,body=False):
 if not isinstance(d,dict):raise Error("invalid_issue_identity")
 number=d.get("number");url=d.get("repository_url")
 if type(number) is not int or number<1 or not isinstance(url,str) or not url.startswith(API+"/repos/"):
  raise Error("invalid_issue_identity")
 r=url[len(API+"/repos/"):]
 try:repo(r)
 except ValueError:raise Error("invalid_issue_identity")
 actual="pr" if "pull_request" in d else "issue"
 if (expected_repo is not None and r.lower()!=expected_repo.lower()) or (expected_number is not None and number!=expected_number) or (kind is not None and actual!=kind):
  raise Error("invalid_issue_identity")
 if d.get("state") not in ("open","closed") or not isinstance(d.get("title"),str):raise Error("invalid_issue_identity")
 if d.get("html_url")!=f"https://github.com/{r}/{'pull' if actual=='pr' else 'issues'}/{number}":raise Error("invalid_issue_identity")
 if d.get("user") is not None and not isinstance(d["user"],dict):raise Error("invalid_issue_identity")
 row={"repository":r,"number":number,"kind":actual,"title":d["title"],"state":d["state"],
      "state_reason":d.get("state_reason"),"url":d["html_url"],"author":(d.get("user") or {}).get("login"),
      "created_at":d.get("created_at"),"updated_at":d.get("updated_at"),"closed_at":d.get("closed_at"),
      "labels":sorted(x.get("name") for x in (d.get("labels") or []) if isinstance(x,dict) and isinstance(x.get("name"),str))}
 if actual=="pr":row["draft"]=d.get("draft")
 if body:
  if d.get("body") is not None and not isinstance(d["body"],str):raise Error("invalid_issue_body")
  row["body"]=d.get("body")
 return row

def issue(r,n,transport="auto",timeout=30):
 """Read an exact Issue, rejecting PRs returned by the shared REST endpoint."""
 r=repo(r)
 if type(n) is not int or n<1:raise ValueError("number must be positive")
 d=request("GET",f"repos/{r}/issues/{n}",transport=transport,timeout=timeout)
 return {"schema":"gh-identity-issue/1",**_issue_row(d,r,n,"issue",True),"observed_at":now()}

class _ContentHTML(HTMLParser):
 """Small structural reader; deliberately not a browser/CSS engine."""
 VOID={"area","base","br","col","embed","hr","img","input","link","meta","param","source","track","wbr"}
 def __init__(self):
  super().__init__(convert_charrefs=True);self.root={"tag":"document","attrs":{},"children":[]};self.stack=[self.root]
 def handle_starttag(self,tag,attrs):
  node={"tag":tag,"attrs":dict(attrs),"children":[]};self.stack[-1]["children"].append(node)
  if tag not in self.VOID:
   if len(self.stack)>=256:raise ValueError("HTML nesting limit exceeded")
   self.stack.append(node)
 def handle_startendtag(self,tag,attrs):
  self.handle_starttag(tag,attrs)
  if tag not in self.VOID:self.handle_endtag(tag)
 def handle_endtag(self,tag):
  for i in range(len(self.stack)-1,0,-1):
   if self.stack[i]["tag"]==tag:del self.stack[i:];break
 def handle_data(self,data):self.stack[-1]["children"].append(data)



def _inline_hidden(style):
 """Limited inline declaration order, not computed CSS or inherited visibility."""
 # Keep semicolons inside strings/functions and comments out of the declaration
 # stream. Unsupported values remain unknown rather than implying visibility.
 declarations=[];quote=None;depth=0;index=0;clean=[]
 while index<len(style):
  char=style[index]
  if quote:
   clean.append(char)
   if char=="\\" and index+1<len(style):
    index+=1;clean.append(style[index])
   elif char==quote:quote=None
  elif style.startswith("/*",index):
   end=style.find("*/",index+2)
   if end<0:break
   clean.append(" ");index=end+1
  elif char in "\"'":quote=char;clean.append(char)
  elif char in "([{":depth+=1;clean.append(char)
  elif char in ")]}":depth=max(0,depth-1);clean.append(char)
  elif char==";" and not depth:declarations.append("".join(clean));clean=[]
  else:clean.append(char)
  index+=1
 declarations.append("".join(clean));values={}
 for declaration in declarations:
  name,sep,value=declaration.partition(":");name=name.strip().lower()
  if not sep or name not in ("display","visibility"):continue
  value=value.strip().lower();important=bool(re.search(r"!\s*important$",value))
  value=re.sub(r"\s*!\s*important$","",value).strip()
  if not value:continue
  if important or not values.get(name,(None,False))[1]:values[name]=(value,important)
 return values.get("display",(None,False))[0]=="none" or values.get("visibility",(None,False))[0]=="hidden"


def html_content(html,selectors,*,source_kind="html",max_bytes=10_000_000,include_controls=False):
 """Extract one explicitly selected body from saved HTML or serialized DOM.

 Selectors are ordered simple #id, .class or tag alternatives, not general CSS.
 Ambiguous matches fail; no whole-page fallback or network access occurs.
 """
 if not isinstance(html,str) or type(max_bytes) is not int or max_bytes<1:raise ValueError("invalid HTML input/limit")
 if len(html.encode("utf-8"))>max_bytes:raise ValueError("HTML byte limit exceeded")
 if type(include_controls) is not bool:raise ValueError("include_controls must be bool")
 if source_kind not in ("html","dom"):raise ValueError("source_kind must be html or dom")
 if not isinstance(selectors,(list,tuple)) or not selectors:raise ValueError("selectors required")
 for selector in selectors:
  if not isinstance(selector,str) or not re.fullmatch(r"[#.]?[A-Za-z_][A-Za-z0-9_:-]*",selector):raise ValueError("only simple #id, .class or tag selectors supported")
 parser=_ContentHTML();parser.feed(html);parser.close()
 def hidden(node):
  a=node["attrs"];style=a.get("style") or ""
  return (node["tag"] in {"script","style","template","noscript","nav","header","footer","svg"}
          or (node["tag"]=="button" and not include_controls)
          or "hidden" in a or (a.get("aria-hidden") or "").lower()=="true"
          or _inline_hidden(style))
 nodes=[]
 def walk(node):
  if hidden(node):return
  nodes.append(node)
  for child in node["children"]:
   if isinstance(child,dict):walk(child)
 walk(parser.root)
 selected=None;used=None
 for selector in selectors:
  def matches(n):
   a=n["attrs"]
   if selector.startswith("#"):return a.get("id")==selector[1:]
   if selector.startswith("."):return selector[1:] in (a.get("class") or "").split()
   return n["tag"]==selector.lower()
  found=[n for n in nodes if matches(n)]
  if len(found)>1:raise Error("ambiguous_html_body")
  if found:selected=found[0];used=selector;break
 if selected is None:raise Error("html_body_not_found")
 blocks={"p","div","section","article","h1","h2","h3","h4","h5","h6","ul","ol","li","blockquote","tr","table"}
 def render(node,pre=False):
  if isinstance(node,str):return node if pre else re.sub(r"\s+"," ",node)
  if hidden(node):return ""
  tag=node["tag"]
  if tag=="br":return "\n"
  if tag=="img":return node["attrs"].get("alt") or ""
  body="".join(render(child,pre or tag=="pre") for child in node["children"])
  if tag=="pre":return "\n"+body+"\n"
  if tag in ("td","th"):return body+"\t"
  if tag in blocks:return "\n"+body+"\n"
  return body
 body=("".join(render(child,True) for child in selected["children"])
       if selected["tag"]=="pre" else render(selected).strip("\n"))
 return {"schema":"gh-identity-html-content/1","body":body,"selector":used,
         "source_kind":source_kind,"source_sha256":hashlib.sha256(html.encode("utf-8")).hexdigest(),
         "visibility":"structural_only","include_controls":include_controls,"identity_verified":False,"observed_at":now()}


def _html_content_file(path,selectors,source_kind,max_bytes,include_controls=False):
 if type(max_bytes) is not int or max_bytes<1:raise ValueError("invalid HTML byte limit")
 with open(path,"rb") as stream:raw=stream.read(max_bytes+1)
 if len(raw)>max_bytes:raise ValueError("HTML byte limit exceeded")
 return html_content(raw.decode("utf-8"),selectors,source_kind=source_kind,max_bytes=max_bytes,include_controls=include_controls)

def content(r,kind,identifier,transport="auto",timeout=30):
 """Read exact PR/Issue body or full commit message; no comments or code diff."""
 r=repo(r)
 if kind in ("pr","issue"):
  if type(identifier) is not int or identifier<1:raise ValueError("number must be positive")
  d=request("GET",f"repos/{r}/issues/{identifier}",transport=transport,timeout=timeout)
  # GitHub's Issues endpoint includes PR descriptions. Validate the kind and
  # exact repository/number rather than silently treating an Issue as a PR.
  if not isinstance(d,dict) or "body" not in d:raise Error("invalid_content")
  row=_issue_row(d,r,identifier,kind,True)
 elif kind=="commit":
  if not isinstance(identifier,str) or re.fullmatch(r"[0-9a-fA-F]{40}",identifier) is None:
   raise ValueError("commit requires a full SHA; resolve refs before reading")
  sha=identifier.lower()
  d=request("GET",f"repos/{r}/commits/{sha}",transport=transport,timeout=timeout)
  if not isinstance(d,dict) or d.get("sha")!=sha or not isinstance(d.get("html_url"),str) or d["html_url"].lower()!=f"https://github.com/{r}/commit/{sha}".lower():
   raise Error("invalid_commit_identity")
  commit=d.get("commit")
  if not isinstance(commit,dict) or not isinstance(commit.get("message"),str):raise Error("invalid_content")
  message=commit["message"]
  row={"repository":r,"kind":"commit","sha":sha,"url":d["html_url"],
       "title":message.split("\n",1)[0],"body":message}
 else:raise ValueError("kind must be pr, issue or commit")
 return {"schema":"gh-identity-content/1",**row,"observed_at":now(),
         "content_scope":"description" if kind!="commit" else "commit_message"}

def content_from_hit(hit,transport="auto",timeout=30):
 """Re-read a selected search/discovery identity; never trust its stale body."""
 if not isinstance(hit,dict):raise ValueError("hit must be an identity object")
 kind=hit.get("kind");r=hit.get("repository")
 if not isinstance(r,str):raise ValueError("hit requires repository")
 identifier=hit.get("sha") if kind=="commit" else hit.get("number")
 return content(r,kind,identifier,transport=transport,timeout=timeout)

def select_content(observation,fields=("title","body")):
 """Select explicit top-level fields offline, retaining identity/provenance."""
 if not isinstance(observation,dict) or observation.get("schema")!="gh-identity-content/1":
  raise ValueError("expected a content observation")
 if not isinstance(fields,(tuple,list)) or any(not isinstance(f,str) or f not in observation for f in fields):
  raise ValueError("unknown content field")
 identity=("schema","repository","kind","number","sha","url","observed_at","content_scope")
 return {key:observation[key] for key in dict.fromkeys((*identity,*fields)) if key in observation}

def issues(r,state="open",max_items=100,max_pages=10,transport="auto",timeout=30):
 """List repository Issues, excluding PRs while charging all fetched rows."""
 r=repo(r)
 if state not in ("open","closed","all"):raise ValueError("invalid state")
 for v in (max_items,max_pages):
  if type(v) is not int or v<1:raise ValueError("limits must be positive integers")
 rows=[];seen=set();complete=False;fetched=0
 path=f"repos/{r}/issues?state={state}&sort=updated&direction=desc&per_page=100&page=1"
 while path and fetched<max_pages:
  batch,headers=_page(path,transport,timeout);fetched+=1
  if not isinstance(batch,list):raise Error("invalid_json")
  _BUDGET.get().charge("items",len(batch))
  for index,d in enumerate(batch):
   row=_issue_row(d,r)
   if row["number"] in seen:raise Error("pagination_incomplete")
   seen.add(row["number"])
   if row["kind"]=="pr":continue
   rows.append(row)
   if len(rows)==max_items:break
  next_path=_next_page(path,headers,len(batch))
  consumed=not batch or index==len(batch)-1
  complete=consumed and next_path is None
  if len(rows)>=max_items or complete:break
  path=next_path
 return {"schema":"gh-identity-issue-discovery/1","repository":r,"state":state,"issues":rows,
         "count":len(rows),"pages_fetched":fetched,"complete":complete,"truncated":not complete}

def search(query,kind="pr",sort="updated",order="desc",max_items=100,max_pages=10,transport="auto",timeout=30):
 """Bounded cross-repository Issue/PR search; never a fleet enumeration claim.

 Pass GitHub qualifiers (repo:, org:, user:, is:open/closed, label:, author:,
 head:, base:, is:merged/unmerged, draft:, created:, updated:) in query.
 """
 if not isinstance(query,str) or not query.strip():raise ValueError("query is required")
 if kind not in ("pr","issue") or sort not in ("updated","created","comments","best-match") or order not in ("asc","desc"):
  raise ValueError("invalid search selection")
 for v in (max_items,max_pages):
  if type(v) is not int or v<1:raise ValueError("limits must be positive integers")
 effective=query.strip()+" is:"+kind
 params={"q":effective,"order":order,"per_page":100}
 if sort!="best-match":params["sort"]=sort
 rows=[];seen=set();total=None;incomplete=False;complete=False;fetched=0;raw_count=0
 for page in range(1,min(max_pages,10)+1):
  params["page"]=page
  data,_headers=_page("search/issues?"+urllib.parse.urlencode(params),transport,timeout);fetched+=1
  if not isinstance(data,dict) or type(data.get("total_count")) is not int or data["total_count"]<0 or type(data.get("incomplete_results")) is not bool or not isinstance(data.get("items"),list):raise Error("invalid_search_response")
  if total is not None and total!=data["total_count"]:raise Error("pagination_incomplete")
  total=data["total_count"];incomplete=incomplete or data["incomplete_results"];batch=data["items"]
  if len(batch)>100:raise Error("invalid_search_response")
  _BUDGET.get().charge("items",len(batch));raw_count+=len(batch)
  if raw_count>total:raise Error("pagination_incomplete")
  for d in batch:
   row=_issue_row(d,kind=kind);key=(row["repository"].lower(),row["number"])
   if key in seen:raise Error("pagination_incomplete")
   seen.add(key)
   if len(rows)<max_items:rows.append(row)
  complete=not incomplete and len(rows)==total
  if complete or len(rows)>=max_items or len(batch)<100:break
 return {"schema":"gh-identity-search/1","kind":kind,"query":effective,"sort":sort,"order":order,
         "items":rows,"count":len(rows),"total_count":total,"pages_fetched":fetched,
         "incomplete_results":incomplete,"complete":complete,"truncated":not complete,
         "scope":"accessible_search_results","observed_at":now()}


def run_history(r,max_items=100,max_pages=10,head_sha=None,branch=None,event=None,transport="auto",timeout=30,exclude_events=()):
 r=repo(r)
 if not isinstance(max_items,int) or max_items<1:raise ValueError("max_items must be positive")
 if not isinstance(max_pages,int) or max_pages<1:raise ValueError("max_pages must be positive")
 rows=[];page=1;exhausted=False;pages_fetched=0;excluded=0
 exclude_events=tuple(exclude_events)
 while page<=max_pages and len(rows)<max_items:
  query={"per_page":"100","page":str(page)}
  # Server-side filters keep pages small; the client-side checks below still verify them.
  for key,value in (("head_sha",head_sha),("branch",branch),("event",event)):
   if value is not None:query[key]=value
  batch,_headers=_page(f"repos/{r}/actions/runs?"+urllib.parse.urlencode(query),transport=transport,timeout=timeout)
  pages_fetched += 1
  if not isinstance(batch,dict) or not isinstance(batch.get("workflow_runs"),list):raise Error("invalid_json")
  raw=batch["workflow_runs"]
  _BUDGET.get().charge("items",len(raw))
  consumed=0
  for x in raw:
   consumed+=1
   row={"run_id":x.get("id"),"attempt":x.get("run_attempt"),"workflow_id":x.get("workflow_id"),"name":x.get("name"),
    "status":x.get("status"),"conclusion":x.get("conclusion"),"head_sha":x.get("head_sha"),"head_branch":x.get("head_branch"),
    "event":x.get("event"),"created_at":x.get("created_at"),"updated_at":x.get("updated_at"),"run_started_at":x.get("run_started_at"),"url":x.get("html_url")}
   if head_sha is not None and row["head_sha"]!=head_sha:continue
   if branch is not None and row["head_branch"]!=branch:continue
   if event is not None and row["event"]!=event:continue
   if row["event"] in exclude_events:excluded+=1;continue
   rows.append(row)
   if len(rows)>=max_items:break
  if len(raw)<100:exhausted=consumed==len(raw);break
  page+=1
 bad={"failure","cancelled","timed_out","action_required","startup_failure","stale"}
 if not rows:state="missing"
 elif any(x["status"]!="completed" for x in rows):state="pending"
 elif any(x["conclusion"] in bad for x in rows):state="failed"
 else:state="green"
 return {"schema":"gh-identity-run-discovery/1","repository":r,"runs":rows,"count":len(rows),
  "pages_fetched":pages_fetched,"complete":exhausted,"truncated":not exhausted,
  "state":state if exhausted else "incomplete",
  "by_conclusion":dict(sorted(Counter(x["conclusion"] or x["status"] or "unknown" for x in rows).items())),
  "filters":{"head_sha":head_sha,"branch":branch,"event":event,"exclude_events":list(exclude_events)},
  "excluded_count":excluded}

def workflow_run_discovery(r, workflow_id, *, branch=None, head_sha=None, event=None,
                           max_items=100, max_pages=10, page_size=100, start_page=1, start_offset=0,
                           transport="auto", timeout=30):
 """Find runs of one verified workflow with bounded, resumable pagination."""
 r=repo(r)
 for label,value in (("max_items",max_items),("max_pages",max_pages),("page_size",page_size),("start_page",start_page)):
  if type(value) is not int or value<1:raise ValueError("invalid "+label)
 if page_size>100:raise ValueError("page_size exceeds 100")
 if type(start_offset) is not int or not 0<=start_offset<page_size:raise ValueError("invalid start_offset")
 if head_sha is not None and (not isinstance(head_sha,str) or not re.fullmatch(r"[0-9a-fA-F]{40}",head_sha)):
  raise ValueError("invalid head_sha")
 for label,value in (("branch",branch),("event",event)):
  if value is not None and (not isinstance(value,str) or not value or any(c in value for c in "\r\n")):
   raise ValueError("invalid "+label)
 info=workflow(r,workflow_id,transport=transport,timeout=timeout)
 wid=info.get("id")
 if type(wid) is not int or wid<1:raise Error("invalid_workflow_identity")
 if str(workflow_id).isdigit() and str(wid)!=str(workflow_id):raise Error("workflow_identity_mismatch")
 rows=[];page=start_page;fetched=0;exhausted=False;seen=set();offset=start_offset;next_offset=0
 while fetched<max_pages and len(rows)<max_items:
  query={"per_page":str(page_size),"page":str(page)}
  if branch is not None:query["branch"]=branch
  if event is not None:query["event"]=event
  # Without this the API returns every run and the client filter pages
  # through megabytes until bytes_limit; the client check below still verifies.
  if head_sha is not None:query["head_sha"]=head_sha
  path=f"repos/{r}/actions/workflows/{wid}/runs?"+urllib.parse.urlencode(query)
  data,_headers=_page(path,transport=transport,timeout=timeout)
  if not isinstance(data,dict) or not isinstance(data.get("workflow_runs"),list):raise Error("invalid_json")
  batch=data["workflow_runs"]
  if len(batch)>page_size:raise Error("invalid_json")
  if offset and len(batch)<offset:raise Error("stale_continuation")
  _BUDGET.get().charge("items",len(batch))
  fetched+=1
  for index,item in enumerate(batch):
   if not isinstance(item,dict) or type(item.get("id")) is not int or type(item.get("run_attempt")) is not int:
    raise Error("invalid_run_identity")
   if item.get("workflow_id")!=wid:raise Error("workflow_identity_mismatch")
   if index<offset:continue
   key=(item["id"],item["run_attempt"])
   if key in seen:raise Error("duplicate_run_identity")
   seen.add(key)
   if head_sha is not None and item.get("head_sha")!=head_sha:continue
   if branch is not None and item.get("head_branch")!=branch:continue
   if event is not None and item.get("event")!=event:continue
   rows.append({"run_id":item["id"],"attempt":item["run_attempt"],"workflow_id":wid,
    "head_sha":item.get("head_sha"),"head_branch":item.get("head_branch"),
    "event":item.get("event"),"status":item.get("status"),"conclusion":item.get("conclusion"),
    "created_at":item.get("created_at"),"url":item.get("html_url")})
   if len(rows)>=max_items:
    next_offset=index+1
    break
  offset=0
  if next_offset and next_offset<len(batch):break
  page+=1
  next_offset=0
  if len(batch)<page_size:
   exhausted=True
   break
 return {"schema":"gh-identity-workflow-runs/1","repository":r,"workflow_id":wid,
  "workflow_path":info.get("path"),"runs":rows,"count":len(rows),
  "pages_fetched":fetched,"complete":exhausted,"truncated":not exhausted,
  "limit_reason":None if exhausted else ("max_items" if len(rows)>=max_items else "max_pages"),
  "next_page":None if exhausted else page,"next_offset":None if exhausted else next_offset,
  "filters":{"branch":branch,"head_sha":head_sha,"event":event}}


def runs(r,limit=20,transport="auto",timeout=30):
 r=repo(r);d=request("GET",f"repos/{r}/actions/runs?per_page={min(100,max(1,limit))}",transport=transport,timeout=timeout)
 ds=[{"run_id":x.get("id"),"attempt":x.get("run_attempt"),"status":x.get("status"),"conclusion":x.get("conclusion"),"head_sha":x.get("head_sha"),"event":x.get("event"),"url":x.get("html_url")} for x in (d.get("workflow_runs")or[])[:limit]]
 return {"schema":"gh-identity-runs/1","repository":r,"runs":ds}
def variable(r,name,transport="auto",timeout=30):
 r=repo(r);d=request("GET",f"repos/{r}/actions/variables/{urllib.parse.quote(name,safe='')}",transport=transport,timeout=timeout)
 return {"schema":"gh-identity-variable/1","repository":r,"name":d.get("name"),"value":d.get("value"),"updated_at":d.get("updated_at")}
def set_variable(r,name,value,write=False,transport="auto",timeout=30):
 r=repo(r);out={"schema":"gh-identity-variable-write/1","repository":r,"name":name,"status":"planned","mutation_status":"not_attempted","verification_status":"not_attempted"}
 if not write:return out
 try:
  try:request("PATCH",f"repos/{r}/actions/variables/{urllib.parse.quote(name,safe='')}",{"name":name,"value":value},transport,timeout,True)
  except Error as e:
   if e.code!="not_found_or_inaccessible":raise
   request("POST",f"repos/{r}/actions/variables",{"name":name,"value":value},transport,timeout,True)
 except Error as e:
  out.update(status="mutation_uncertain" if e.uncertain else "mutation_failed",mutation_status="uncertain" if e.uncertain else "failed",error=e.code)
  return out
 out["mutation_status"]="succeeded"
 try:
  got=variable(r,name,transport,timeout)
 except Error as e:
  out.update(status="verification_failed",verification_status="failed",verification_error=e.code)
  return out
 out["verified"]=got.get("value")==value
 out["verification_status"]="verified" if out["verified"] else "mismatch"
 out["status"]="verified" if out["verified"] else "verification_mismatch"
 return out

def _label_names(values,name):
 out=[]
 for v in values or ():
  if not isinstance(v,str) or not v.strip() or len(v)>100 or any(c in v for c in "\r\n\x00"):raise ValueError(f"invalid {name} label")
  if v not in out:out.append(v)
 return out

def set_labels(r,n,add=(),remove=(),write=False,transport="auto",timeout=30):
 """Intentionally add/remove named labels on one Issue/PR; planned unless write.

 Only labels that already exist in the repository can be added (never created).
 The receipt shows before, target and after, verified by reading the issue back.
 """
 r=repo(r);n=_positive_identifier(n,"number")
 add=_label_names(add,"add");remove=_label_names(remove,"remove")
 if not add and not remove:raise ValueError("give --add and/or --remove")
 if set(add)&set(remove):raise ValueError("a label cannot be both added and removed")
 issue_path=f"repos/{r}/issues/{n}"
 before=_issue_row(request("GET",issue_path,transport=transport,timeout=timeout),r,n)["labels"]
 missing=[]
 for name in add:
  if name in before:continue
  try:request("GET",f"repos/{r}/labels/{urllib.parse.quote(name,safe='')}",transport=transport,timeout=timeout)
  except Error as e:
   if e.code!="not_found_or_inaccessible":raise
   missing.append(name)
 to_add=[x for x in add if x not in before];to_remove=[x for x in remove if x in before]
 target=sorted((set(before)|set(add))-set(remove))
 out={"schema":"gh-identity-label-write/1","repository":r,"number":n,"before":before,"target":target,
      "add":to_add,"remove":to_remove,"missing_labels":missing,
      "mutation_status":"not_attempted","verification_status":"not_attempted"}
 if missing:return {**out,"status":"rejected_missing_labels"}
 if not to_add and not to_remove:return {**out,"status":"already_satisfied","after":before}
 if not write:return {**out,"status":"planned"}
 try:
  if to_add:request("POST",issue_path+"/labels",{"labels":to_add},transport,timeout,True)
  for name in to_remove:request("DELETE",issue_path+"/labels/"+urllib.parse.quote(name,safe=''),None,transport,timeout,True)
 except Error as e:
  out.update(status="mutation_uncertain" if e.uncertain else "mutation_failed",mutation_status="uncertain" if e.uncertain else "failed",error=e.code)
  return out
 out["mutation_status"]="succeeded"
 try:after=_issue_row(request("GET",issue_path,transport=transport,timeout=timeout),r,n)["labels"]
 except Error as e:
  out.update(status="verification_failed",verification_status="failed",verification_error=e.code);return out
 ok=after==target
 out.update(after=after,verified=ok,verification_status="verified" if ok else "mismatch",status="verified" if ok else "verification_mismatch")
 return out

def post_comment(r,n,body,write=False,marker=None,transport="auto",timeout=30,sanitize_mentions=False):
 r=repo(r)
 if not body.strip():raise ValueError("empty body")
 if re.search(r"(^|[^A-Za-z0-9_])@[A-Za-z0-9_-]+",body):
  if sanitize_mentions:body=re.sub(r"(^|[^A-Za-z0-9_])@(?=[A-Za-z0-9_-]+)",lambda m:m.group(1)+"＠",body)
  else:raise ValueError("comment contains an active mention")
 if write and not marker:raise ValueError("marker is required for comment writes")
 if marker:
  if not re.fullmatch(r"<!-- gh-identity:[A-Za-z0-9_.:-]+ -->",marker):raise ValueError("invalid marker")
  for x in pages(f"repos/{r}/issues/{n}/comments",transport,timeout):
   if marker in (x.get("body")or""):return {"schema":"gh-identity-comment-write/1","status":"already_exists","id":x.get("id"),"url":x.get("html_url"),"marker":marker}
  body=marker+"\n"+body
 if not write:return {"schema":"gh-identity-comment-write/1","status":"planned","chars":len(body),"marker":marker,"body":body}
 out={"schema":"gh-identity-comment-write/1","marker":marker,"mutation_status":"not_attempted","verification_status":"not_attempted"}
 try:
  d=request("POST",f"repos/{r}/issues/{n}/comments",{"body":body},transport,timeout,True)
 except Error as e:
  out.update(status="mutation_uncertain" if e.uncertain else "mutation_failed",mutation_status="uncertain" if e.uncertain else "failed",error=e.code)
  return out
 # A successful transport is not evidence of a valid comment identity.
 cid=d.get("id") if isinstance(d,dict) else None
 if type(cid) is not int or cid<1:
  out.update(status="mutation_uncertain",mutation_status="uncertain",error="invalid_comment_response")
  return out
 out.update(id=cid,mutation_status="succeeded")
 try:
  got=request("GET",f"repos/{r}/issues/comments/{cid}",transport=transport,timeout=timeout)
 except Error as e:
  out.update(status="verification_failed",verification_status="failed",verification_error=e.code)
  return out
 expected_issue=f"{API}/repos/{r}/issues/{n}"
 expected_url=f"https://github.com/{r}/issues/{n}#issuecomment-{cid}"
 # PR conversation comments may use /pull/ in their human-facing URL.
 urls={expected_url,f"https://github.com/{r}/pull/{n}#issuecomment-{cid}"}
 verified=(isinstance(got,dict) and type(got.get("id")) is int and got.get("id")==cid
           and got.get("issue_url")==expected_issue and got.get("body")==body
           and got.get("html_url") in urls)
 out.update(status="verified" if verified else "verification_mismatch",
            verification_status="verified" if verified else "mismatch",verified=verified)
 if verified:out["url"]=got["html_url"]
 return out

def resolve_ref(r,ref,transport="auto",timeout=30):
 r=repo(r);d=request("GET",f"repos/{r}/commits/{urllib.parse.quote(ref,safe='')}",transport=transport,timeout=timeout)
 sha=d.get("sha") if isinstance(d,dict) else None
 if not isinstance(sha,str) or not re.fullmatch(r"[0-9a-fA-F]{40}",sha):raise Error("invalid_commit")
 return {"schema":"gh-identity-ref/1","repository":r,"ref":ref,"sha":sha.lower(),"observed_at":now()}
def _tree_patterns(values):
 if not isinstance(values,(tuple,list)):raise ValueError("exclusions must be a list of patterns")
 for p in values:
  if not isinstance(p,str) or not p or p.startswith("/") or "\\" in p or any(x in ("",".","..") for x in p.split("/")):
   raise ValueError("exclusions must be repository-relative patterns without trailing slash")
 return tuple(dict.fromkeys(values))

def tree(r,ref="main",*,depth=1,exclude_dirs=(),exclude_files=(),transport="auto",timeout=30):
 """List a pinned repository structure, pruning excluded directories before GET.

    depth=1 is the root only; None walks all included subtrees. Patterns with
    no slash match a basename at any level, otherwise match the full path.
    This fetches metadata only, never blobs, history, symlink or submodule targets.
 """
 r=repo(r)
 if not isinstance(ref,str) or not ref:raise ValueError("ref is required")
 if depth is not None and (type(depth) is not int or depth<1):raise ValueError("depth must be positive or None")
 dirs=_tree_patterns(exclude_dirs);files=_tree_patterns(exclude_files)
 resolved=resolve_ref(r,ref,transport,timeout)
 pending=[("",resolved["sha"],1,frozenset())];rows=[];truncated=False;requests=0;excluded=0;root_sha=None
 modes={"040000":("tree","directory"),"100644":("blob","file"),"100755":("blob","file"),
        "120000":("blob","symlink"),"160000":("commit","submodule")}
 while pending:
  prefix,sha,level,ancestors=pending.pop()
  _BUDGET.get().charge("pages",1)
  d=request("GET",f"repos/{r}/git/trees/{sha}",transport=transport,timeout=timeout);requests+=1
  if not isinstance(d,dict) or not isinstance(d.get("sha"),str) or re.fullmatch(r"[0-9a-f]{40}",d["sha"]) is None:
   raise Error("invalid_tree_identity")
  if prefix and d["sha"]!=sha:raise Error("invalid_tree_identity")
  if d["sha"] in ancestors:raise Error("invalid_tree_cycle")
  if root_sha is None:root_sha=d["sha"]
  entries=d.get("tree")
  if not isinstance(entries,list) or type(d.get("truncated")) is not bool:raise Error("invalid_tree_response")
  _BUDGET.get().charge("items",len(entries))
  truncated=truncated or d["truncated"];seen=set()
  for entry in entries:
   if not isinstance(entry,dict):raise Error("invalid_tree_entry")
   name=entry.get("path");mode=entry.get("mode");ident=entry.get("sha")
   if not isinstance(name,str) or not name or name in (".","..") or "/" in name or "\0" in name or name in seen:
    raise Error("invalid_tree_entry")
   seen.add(name)
   if not isinstance(mode,str) or mode not in modes or entry.get("type")!=modes[mode][0] or not isinstance(ident,str) or re.fullmatch(r"[0-9a-f]{40}",ident) is None:
    raise Error("invalid_tree_entry")
   path=prefix+name;kind=modes[mode][1]
   patterns=dirs if kind=="directory" else files
   if any(fnmatch.fnmatchcase(path if "/" in p else name,p) for p in patterns):
    excluded+=1;continue
   item={"path":path,"kind":kind,"type":entry["type"],"mode":mode,"sha":ident}
   if "size" in entry:
    if type(entry["size"]) is not int or entry["size"]<0:raise Error("invalid_tree_entry")
    item["size"]=entry["size"]
   if kind=="directory":
    item["expanded"]=depth is None or level<depth
    if item["expanded"]:pending.append((path+"/",ident,level+1,ancestors|{d["sha"]}))
   rows.append(item)
 return {"schema":"gh-identity-tree/1","repository":r,"ref":ref,"commit_sha":resolved["sha"],
         "tree_sha":root_sha,"depth":depth,"exclude_dirs":list(dirs),"exclude_files":list(files),
         "entries":sorted(rows,key=lambda x:x["path"]),"count":len(rows),"excluded_entries":excluded,
         "tree_requests":requests,"scope":"selected_depth_and_exclusions","complete":not truncated,
         "truncated":truncated,"observed_at":now()}

def source_identity(r,ref,path,transport="auto",timeout=30):
 r=repo(r)
 if not isinstance(path,str) or not path or path.startswith("/") or "\\" in path or any(p in ("",".","..") for p in path.split("/")):
  raise ValueError("invalid source path")
 resolved=resolve_ref(r,ref,transport,timeout)
 encoded="/".join(urllib.parse.quote(p,safe="") for p in path.split("/"))
 d=request("GET",f"repos/{r}/contents/{encoded}?ref={resolved['sha']}",transport=transport,timeout=timeout)
 if not isinstance(d,dict) or d.get("type")!="file" or d.get("submodule_git_url"):raise Error("source_not_regular_file")
 tree=request("GET",f"repos/{r}/git/trees/{resolved['sha']}?recursive=1",transport=transport,timeout=timeout)
 entries=tree.get("tree") if isinstance(tree,dict) else None
 if not isinstance(entries,list):raise Error("invalid_json")
 matches=[x for x in entries if isinstance(x,dict) and x.get("path")==path]
 if len(matches)!=1 or matches[0].get("type")!="blob" or matches[0].get("mode") not in ("100644","100755"):
  raise Error("source_not_regular_file")
 if matches[0].get("sha")!=d.get("sha"):raise Error("source_identity_mismatch")
 blob=d.get("sha");size=d.get("size")
 if not isinstance(blob,str) or not re.fullmatch(r"[0-9a-fA-F]{40}",blob):raise Error("invalid_blob")
 if not isinstance(size,int) or size<0:raise Error("invalid_source_size")
 return {"schema":"gh-identity-source/1","repository":r,"ref":ref,"commit_sha":resolved["sha"],"path":path,"blob_sha":blob.lower(),"size":size,"type":"file","observed_at":now()}
def _min_checks(value):
 if not isinstance(value,int) or isinstance(value,bool) or value<1:raise ValueError("min_checks must be at least 1")
 return value

def summarize_checks(rows,expected_count,min_checks=1):
 min_checks=_min_checks(min_checks)
 if not isinstance(rows,list):raise ValueError("check rows must be a list")
 normalized=[{"id":x.get("id"),"name":x.get("name"),"status":x.get("status"),"conclusion":x.get("conclusion"),"url":x.get("html_url") or x.get("url"),"annotations_count":x.get("annotations_count",(x.get("output")or{}).get("annotations_count",0))} for x in rows]
 complete=isinstance(expected_count,int) and not isinstance(expected_count,bool) and expected_count==len(normalized)
 bad={"failure","cancelled","timed_out","action_required","startup_failure","stale"}
 if not complete:state="incomplete"
 elif len(normalized)<min_checks:state="pending"
 elif any(x["status"]!="completed" for x in normalized):state="pending"
 elif any(x["conclusion"] in bad for x in normalized):state="failed"
 elif not any(x["conclusion"]=="success" for x in normalized):state="failed"
 else:state="green"
 by_conclusion=dict(sorted(Counter(x["conclusion"] or x["status"] or "unknown" for x in normalized).items()))
 return {"state":state,"complete":complete,"expected_count":expected_count,"count":len(normalized),"min_checks":min_checks,"by_conclusion":by_conclusion,"checks":normalized}

def checks_for_sha(r,sha,min_checks=1,transport="auto",timeout=30,*,requester=None):
 min_checks=_min_checks(min_checks);r=repo(r);rows=[];expected=None
 path=f"repos/{r}/commits/{sha}/check-runs?per_page=100&page=1"
 while path:
  d,headers=_page(path,transport,timeout,requester)
  if not isinstance(d,dict) or not isinstance(d.get("check_runs"),list):raise Error("invalid_json")
  total=d.get("total_count")
  if type(total) is not int or total<0:raise Error("pagination_incomplete")
  if expected is None:expected=total
  elif total!=expected:raise Error("pagination_incomplete")
  batch=d["check_runs"]
  _BUDGET.get().charge("items",len(batch));rows.extend(batch)
  if len(rows)>expected:raise Error("pagination_incomplete")
  path=_next_page(path,headers,len(batch))
 summary=summarize_checks(rows,expected,min_checks)
 return {"schema":"gh-identity-checks/1","repository":r,"sha":sha,**summary,"observed_at":now()}

def render_list(o):
 """One line per PR or Issue: `#N state head_ref title` (PRs) or `#N state [labels] title` (Issues)."""
 out=[]
 for x in o.get("pull_requests") or []:
  out.append(f"#{x['number']} {x['state']}{' draft' if x['draft'] else ''} {x['head_ref']} {x['title']}")
 for x in o.get("issues") or []:
  labels=f" [{','.join(x['labels'])}]" if x.get("labels") else ""
  out.append(f"#{x['number']} {x['state']}{labels} {x['title']}")
 if o.get("truncated"):out.append(f"(truncated after {o['count']})")
 return "\n".join(out)

_GET_PATH=re.compile(r"(?:repos/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+|users/[A-Za-z0-9-]+|orgs/[A-Za-z0-9-]+)(?:/[A-Za-z0-9._~%-]+)*")

def get(path,query=(),pick=(),transport="auto",timeout=30):
 """Read-only GET of one REST path with query options, e.g. ("state","open").

 For endpoints without a dedicated command. The path is a plain repos/users/orgs path (no
 query string, no `..`); options go through `query` so they are URL-encoded. `pick` keeps only
 the given dotted fields of each item (head.ref, user.login) for a compact, bounded result.
 """
 if not isinstance(path,str) or not _GET_PATH.fullmatch(path) or ".." in path.split("/"):raise ValueError("invalid path")
 pairs=[]
 for item in query:
  k,v=item if isinstance(item,(tuple,list)) else (item.split("=",1)+[None])[:2]
  if v is None or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",k):raise ValueError("query must be KEY=VALUE")
  pairs.append((k,v))
 full=path+("?"+urllib.parse.urlencode(pairs) if pairs else "")
 d=request("GET",full,transport=transport,timeout=timeout)
 def one(x):
  if not pick or not isinstance(x,dict):return x
  out={}
  for f in pick:
   v=x
   for part in f.split("."):v=v.get(part) if isinstance(v,dict) else None
   out[f]=v
  return out
 if isinstance(d,list):data=[one(x) for x in d]
 elif isinstance(d,dict) and pick and any(isinstance(d.get(k),list) for k in ("check_runs","workflow_runs","items","jobs","artifacts")):
  key=next(k for k in ("check_runs","workflow_runs","items","jobs","artifacts") if isinstance(d.get(k),list))
  data=[one(x) for x in d[key]]
 else:data=one(d)
 return {"schema":"gh-identity-get/1","path":path,"query":dict(pairs),"data":data,"observed_at":now()}

def read_comment(targets,kind=None,transport="auto",timeout=30):
 """Read one review/inline/conversation body from a GitHub URL or short IDs."""
 if not isinstance(targets,(list,tuple)) or not targets:raise ValueError("comment target required")
 def positive(value):
  if not re.fullmatch(r"[1-9][0-9]*",str(value)):raise ValueError("positive ID required")
  return int(value)
 if len(targets)==1:
  url=urllib.parse.urlsplit(targets[0])
  match=re.fullmatch(r"/([\w.-]+/[\w.-]+)/(pull|issues)/([1-9][0-9]*)",url.path)
  anchor=re.fullmatch(r"(pullrequestreview-|discussion_r|issuecomment-)([1-9][0-9]*)",url.fragment)
  if url.scheme!="https" or url.netloc!="github.com" or url.query or not match or not anchor:raise ValueError("GitHub comment URL required")
  r=repo(match[1]);number=positive(match[3]);ident=positive(anchor[2])
  actual={"pullrequestreview-":"review","discussion_r":"inline","issuecomment-":"conversation"}[anchor[1]]
  if actual!="conversation" and match[2]!="pull":raise ValueError("PR URL required")
  if kind is not None and kind!=actual:raise ValueError("comment kind mismatch")
  kind=actual
 elif kind=="review" and len(targets)==3:
  r=repo(targets[0]);number=positive(targets[1]);ident=positive(targets[2])
 elif kind=="inline" and len(targets)==2:
  r=repo(targets[0]);ident=positive(targets[1])
 else:raise ValueError("use URL, or review REPO PR ID / inline-comment REPO ID")
 path=(f"repos/{r}/pulls/{number}/reviews/{ident}" if kind=="review" else
       f"repos/{r}/pulls/comments/{ident}" if kind=="inline" else f"repos/{r}/issues/comments/{ident}")
 data=get(path,transport=transport,timeout=timeout)["data"]
 if not isinstance(data,dict) or type(data.get("id")) is not int or data["id"]!=ident or not isinstance(data.get("body"),str):raise Error("invalid_comment_response")
 return {"schema":"gh-identity-comment-body/1","kind":kind,"repository":r,"id":ident,"body":data["body"],"url":data.get("html_url"),"observed_at":now()}

def render_get(o):
 data=o["data"]
 rows=data if isinstance(data,list) else [data]
 return "\n".join(" ".join("-" if v is None else str(v) for v in x.values()) if isinstance(x,dict) else json.dumps(x,ensure_ascii=False) for x in rows)

_QUIET_CHECKS=("success","skipped","neutral")

def checks_for_shas(r,shas,min_checks=1,transport="auto",timeout=30):
 """Several heads at once (e.g. before/after a push). One SHA keeps the single-head schema."""
 if not isinstance(shas,(list,tuple)) or not 1<=len(shas)<=20:raise ValueError("give 1 to 20 SHAs")
 if len(shas)==1:return checks_for_sha(r,shas[0],min_checks,transport,timeout)
 items=[checks_for_sha(r,x,min_checks,transport,timeout) for x in shas]
 state="green" if all(x["state"]=="green" for x in items) else next(x["state"] for x in items if x["state"]!="green")
 return {"schema":"gh-identity-checks-list/1","repository":repo(r),"state":state,"items":items,"observed_at":now()}

def render_checks(o):
 """`uniq -c`-style digest: one header per SHA, then only checks that need attention.

 Rows with the same status, conclusion and name (matrix jobs, re-runs) collapse into one
 counted line, so a 30-job matrix with two failures prints two lines, not thirty.
 """
 out=[]
 for item in o["items"] if "items" in o else [o]:
  counts=" ".join(f"{k}={v}" for k,v in item["by_conclusion"].items()) or "no checks"
  out.append(f"{item['sha'][:12]} {item['state']} {item['count']} {counts}")
  groups=Counter((x["status"],x["conclusion"] or "-",x["name"]) for x in item["checks"]
                 if (x["conclusion"] or x["status"]) not in _QUIET_CHECKS)
  out+=[f"  {n:>3} {status} {conclusion} {name}" for (status,conclusion,name),n in sorted(groups.items(),key=lambda kv:(kv[0][0]=="completed",kv[0][2]))]
 return "\n".join(out)

def pr_status(r,n,*,last=3,exclude_events=("dynamic",),transport="auto",timeout=30):
 """One compact look at a PR head: runs, unfinished/failed checks, latest talk.

 An observation, not a gate (use checks / wait-checks to gate). Runs from the
 excluded events (default: Copilot's "dynamic") are counted, not listed.
 """
 if type(last) is not int or not 0<=last<=20:raise ValueError("last must be 0-20")
 head=pr(r,n,transport=transport,timeout=timeout);sha=head["head_sha"]
 runs=run_history(r,head_sha=sha,exclude_events=exclude_events,transport=transport,timeout=timeout)
 ch=checks_for_sha(r,sha,1,transport,timeout)
 cs=comments(r,n,transport=transport,timeout=timeout)["comments"]
 rs=reviews(r,n,transport=transport,timeout=timeout)["reviews"]
 talk=lambda rows,when:[{k:x.get(k) for k in ("author","state",when,"preview","url") if k in x} for x in rows[-last:]] if last else []
 return {"schema":"gh-identity-pr-status/1","repository":repo(r),"number":n,
  "pr":{k:head[k] for k in ("state","draft","merged","mergeable_state","head_sha","head_ref","base_ref","url")},
  "runs":{"state":runs["state"],"by_conclusion":runs["by_conclusion"],"excluded_count":runs["excluded_count"],
          "runs":[{"name":x["name"],"event":x["event"],"run_id":x["run_id"],"result":x["conclusion"] or x["status"]} for x in runs["runs"]]},
  "checks":{"state":ch["state"],"count":ch["count"],"by_conclusion":ch["by_conclusion"],
            "attention":[{"name":c["name"],"result":c["conclusion"] or c["status"],"url":c["url"]} for c in ch["checks"]
                         if (c["conclusion"] or c["status"]) not in ("success","skipped","neutral")]},
  "comments":talk(cs,"created_at"),"reviews":talk(rs,"submitted_at"),"observed_at":now()}

def item_status(r,n,*,last=3,exclude_events=("dynamic",),transport="auto",timeout=30):
 """PR -> pr_status (with title); Issue -> state, title and latest comments."""
 r=repo(r);n=_positive_identifier(n,"number")
 d=request("GET",f"repos/{r}/issues/{n}",transport=transport,timeout=timeout)
 row=_issue_row(d,r,n)
 if row["kind"]=="pr":
  return {**pr_status(r,n,last=last,exclude_events=exclude_events,transport=transport,timeout=timeout),"kind":"pr","title":row["title"],"labels":row["labels"]}
 if type(last) is not int or not 0<=last<=20:raise ValueError("last must be 0-20")
 cs=comments(r,n,transport=transport,timeout=timeout)["comments"]
 return {"schema":"gh-identity-issue-status/1","repository":r,"number":n,"kind":"issue","title":row["title"],
         "state":row["state"],"labels":row["labels"],"updated_at":d.get("updated_at"),"url":d.get("html_url"),
         "comments":[{k:x.get(k) for k in ("author","created_at","preview","url")} for x in cs[-last:]] if last else [],
         "observed_at":now()}

def parse_status_targets(tokens):
 """OWNER/REPO sets the repository for following bare numbers; OWNER/REPO#N is explicit."""
 current=None;targets=[]
 for token in tokens:
  if re.fullmatch(r"[0-9]+",token):
   if current is None:raise ValueError("a number needs a preceding OWNER/REPO")
   targets.append((current,int(token)))
  elif "#" in token:
   name,_,number=token.partition("#")
   if not re.fullmatch(r"[0-9]+",number):raise ValueError("expected OWNER/REPO#NUMBER")
   targets.append((repo(name),int(number)))
  else:
   current=repo(token)
 if not targets:raise ValueError("no PR or Issue number given")
 if len(targets)>20:raise ValueError("at most 20 targets")
 return targets

def status_many(tokens,*,last=3,exclude_events=("dynamic",),transport="auto",timeout=30):
 """Several PRs/Issues, possibly across repositories; one failure does not hide the rest."""
 items=[]
 for r,n in parse_status_targets(tokens):
  try:items.append(item_status(r,n,last=last,exclude_events=exclude_events,transport=transport,timeout=timeout))
  except (Error,ValueError) as e:items.append({"repository":r,"number":n,"kind":None,"error":getattr(e,"code","invalid_argument")})
 return items[0] if len(items)==1 and "error" not in items[0] else {"schema":"gh-identity-status-list/1","items":items,"observed_at":now()}

def render_status(o):
 out=[]
 for item in o["items"] if "items" in o else [o]:
  if item.get("error"):out.append(f"{item['repository']}#{item['number']}: error {item['error']}")
  elif item.get("kind")=="issue":
   out.append(f"{item['repository']}#{item['number']} issue {item['state']} updated={item['updated_at']} {item['title']}")
   if item.get("labels"):out.append("labels: "+", ".join(item["labels"]))
   out+=[f"comment {x.get('author')} {x.get('created_at')}: {(x.get('preview') or '')[:200]}" for x in item["comments"]]
  else:
   out.append(f"{item['repository']}#{item['number']} {item.get('title') or ''}".rstrip())
   if item.get("labels"):out.append("labels: "+", ".join(item["labels"]))
   out.append(render_pr_status(item))
 return "\n".join(out)

def render_pr_status(o):
 p=o["pr"];lines=[f"#{o['number']} {p['state']}{' draft' if p['draft'] else ''} merged={p['merged']} {p['mergeable_state']} head={p['head_sha'][:8]} {p['head_ref']} -> {p['base_ref']}"]
 r=o["runs"];lines.append(f"runs: {r['state']} {r['by_conclusion']}"+(f" (excluded {r['excluded_count']})" if r["excluded_count"] else ""))
 lines+=[f"  {x['name']} {x['event']} {x['run_id']} {x['result']}" for x in r["runs"]]
 c=o["checks"];lines.append(f"checks: {c['state']} {c['count']} {c['by_conclusion']}")
 lines+=[f"  ! {x['name']} {x['result']}" for x in c["attention"]]
 for label,rows,when in (("comment",o["comments"],"created_at"),("review",o["reviews"],"submitted_at")):
  lines+=[f"{label} {x.get('author')} {x.get(when)}{' '+x['state'] if x.get('state') else ''}: {(x.get('preview') or '')[:200]}" for x in rows]
 return "\n".join(lines)

def _checks_snapshot(summary,elapsed):
 return {"at_seconds":round(elapsed,1),"state":summary["state"],"count":summary["count"],"by_conclusion":summary["by_conclusion"]}

def _checks_progress(summary,ignored,elapsed,changed):
 counts=" ".join(f"{k}={v}" for k,v in summary["by_conclusion"].items()) or "no checks"
 skip=f" (ignored {len(ignored)})" if ignored else ""
 lines=[f"[{int(elapsed)}s] {summary['state']} {counts}{skip}"]
 lines+=[f"  {c['name']}: {c['from']} -> {c['to']}" for c in changed]
 waiting=[c["name"] for c in summary["checks"] if c["status"]!="completed"]
 if waiting:lines.append(f"  waiting ({len(waiting)}): {', '.join(waiting[:5])}{' ...' if len(waiting)>5 else ''}")
 return "\n".join(lines)

def wait_checks(r,sha,*,min_checks=1,interval=60,wait_timeout=900,ignore=(),transport="auto",timeout=30,
                progress=None,sleep=None,clock=None):
 """Poll check-runs on one SHA until green or failed, or until wait_timeout.

 Reports the first and the last snapshot plus every per-check transition seen
 between polls (queued -> in_progress -> success ...). Each poll is its own
 bounded operation. `ignore` drops named checks (e.g. a reviewer bot that
 errors) before the state is judged; they stay listed.
 """
 if not re.fullmatch(r"[0-9a-fA-F]{40}",str(sha)):raise ValueError("invalid sha")
 if isinstance(interval,bool) or not isinstance(interval,(int,float)) or not 5<=interval<=600:raise ValueError("interval must be 5-600 seconds")
 if isinstance(wait_timeout,bool) or not isinstance(wait_timeout,(int,float)) or not 0<wait_timeout<=3600:raise ValueError("wait_timeout must be 1-3600 seconds")
 if _BUDGET.get() is not None:raise ValueError("wait_checks runs its own bounded polls; call it outside operation()")
 sleep=sleep or time.sleep;clock=clock or time.monotonic
 ignore=set(ignore);start=clock();polls=0;first=None;seen={};transitions=[]
 while True:
  polls+=1
  raw=checks_for_sha(r,sha,min_checks,transport,timeout)
  kept=[c for c in raw["checks"] if c["name"] not in ignore]
  ignored=[c for c in raw["checks"] if c["name"] in ignore]
  expected=raw["expected_count"]-len(ignored) if isinstance(raw["expected_count"],int) else None
  # min_checks counts every check, ignored ones included (as in the README);
  # judge the kept checks against the same total minus what was ignored.
  floor=max(1,min_checks-len(ignored))
  summary=summarize_checks(kept,expected,floor) if raw["complete"] else {**summarize_checks(kept,None,floor),"state":"incomplete"}
  elapsed=clock()-start
  changed=[]
  for c in kept:
   key=c["id"] if c.get("id") is not None else c["name"]
   now_state=c["conclusion"] or c["status"] or "unknown"
   before=seen.get(key)
   if before is not None and before!=now_state:
    changed.append({"name":c["name"],"from":before,"to":now_state,"at_seconds":round(elapsed,1)})
   elif before is None and first is not None:
    changed.append({"name":c["name"],"from":"absent","to":now_state,"at_seconds":round(elapsed,1)})
   seen[key]=now_state
  transitions+=changed
  if first is None:first=_checks_snapshot(summary,elapsed)
  if progress:progress(_checks_progress(summary,ignored,elapsed,changed))
  # A completed failure means this SHA can no longer turn green: stop early.
  bad={"failure","cancelled","timed_out","action_required","startup_failure","stale"}
  failed=[c["name"] for c in kept if c["status"]=="completed" and c["conclusion"] in bad]
  if failed and summary["state"]=="pending":summary={**summary,"state":"failed"}
  done=summary["state"] in ("green","failed")
  if done or elapsed+interval>wait_timeout:
   return {"schema":"gh-identity-wait-checks/1","repository":repo(r),"sha":sha.lower(),**summary,
           "initial":first,"final":_checks_snapshot(summary,elapsed),"transitions":transitions,
           "ignored":[{"name":c["name"],"conclusion":c["conclusion"],"status":c["status"]} for c in ignored],
           "failed_checks":failed,"polls":polls,"elapsed_seconds":round(elapsed,1),"timed_out":not done,"observed_at":now()}
  sleep(interval)

def observe_pr(r,n,min_checks=1,transport="auto",timeout=30):
 before=pr(r,n,transport=transport,timeout=timeout)
 ch=checks_for_sha(r,before["head_sha"],min_checks,transport,timeout)
 cs=comments(r,n,transport=transport,timeout=timeout)
 rs=reviews(r,n,transport=transport,timeout=timeout)
 after=pr(r,n,transport=transport,timeout=timeout)
 return {"schema":"gh-identity-pr-observation/1","repository":repo(r),"number":n,"head_sha":before["head_sha"],"base_sha":before["base_sha"],
  "state":before["state"],"draft":before["draft"],"mergeable":before["mergeable"],"checks":ch,"conversation":cs,"reviews":rs,
  "stale":before["head_sha"]!=after["head_sha"],"head_sha_after":after["head_sha"],"observed_at":now()}
def workflow(r,w,transport="auto",timeout=30):
 r=repo(r);d=request("GET",f"repos/{r}/actions/workflows/{urllib.parse.quote(str(w),safe='')}",transport=transport,timeout=timeout)
 return {"schema":"gh-identity-workflow/1","repository":r,"id":d.get("id"),"name":d.get("name"),"path":d.get("path"),"state":d.get("state"),"url":d.get("html_url"),"observed_at":now()}

def run(r, run_id, attempt=None, transport="auto", timeout=30):
 """Read an exact Actions run and optionally one exact rerun attempt."""
 r=repo(r)
 def positive(x, label):
  if isinstance(x,bool) or not str(x).isdigit() or int(x)<1:raise ValueError("invalid " + label + " identifier")
  return int(x)
 run_id=positive(run_id, "run")
 if attempt is not None:attempt=positive(attempt, "attempt")
 path=f"repos/{r}/actions/runs/{run_id}"
 if attempt is not None:path+=f"/attempts/{attempt}"
 d=request("GET",path,transport=transport,timeout=timeout)
 if not isinstance(d,dict) or d.get("id")!=run_id:raise Error("invalid_json")
 actual=d.get("run_attempt")
 if attempt is not None and actual!=attempt:raise Error("attempt_mismatch")
 return {"schema":"gh-identity-run/1","repository":r,
  "workflow_id":d.get("workflow_id"),"run_id":d["id"],"attempt":actual,
  "head_sha":d.get("head_sha"),"event":d.get("event"),
  "status":d.get("status"),"conclusion":d.get("conclusion"),
  "created_at":d.get("created_at"),"url":d.get("html_url"),"observed_at":now()}


def jobs(r, run_id, attempt=None, transport="auto", timeout=30):
 """Read all jobs and their steps for one Actions run or exact attempt."""
 r=repo(r)
 def ident(value):
  if isinstance(value,bool) or not str(value).isdigit() or int(value)<1:
   raise ValueError("invalid job lookup identifier")
  return int(value)
 run_id=ident(run_id)
 if attempt is not None:attempt=ident(attempt)
 base=f"repos/{r}/actions/runs/{run_id}"
 if attempt is not None:base+=f"/attempts/{attempt}"
 rows=[];page=1;total=None
 while True:
  data,_headers=_page(f"{base}/jobs?per_page=100&page={page}",transport=transport,timeout=timeout)
  if not isinstance(data,dict) or not isinstance(data.get("jobs"),list):raise Error("invalid_json")
  if total is None:total=data.get("total_count")
  batch=data["jobs"];_BUDGET.get().charge("items",len(batch));rows.extend(batch)
  if len(batch)<100:break
  page+=1
  if page>100:raise Error("pagination_incomplete")
 if type(total) is not int or total<0 or len(rows)!=total:raise Error("pagination_incomplete")
 out=[];seen=set()
 for x in rows:
  if not isinstance(x,dict):raise Error("invalid_json")
  jid=x.get("id");actual_run=x.get("run_id");actual_attempt=x.get("run_attempt")
  if type(jid) is not int or jid<1:raise Error("invalid_job_identity")
  if jid in seen:raise Error("ambiguous_job_identity")
  seen.add(jid)
  if type(actual_run) is not int or actual_run!=run_id:raise Error("job_identity_mismatch")
  if type(actual_attempt) is not int or actual_attempt<1:raise Error("invalid_attempt_identity")
  if attempt is not None and actual_attempt!=attempt:raise Error("attempt_mismatch")
  steps=x.get("steps",[])
  if not isinstance(steps,list) or any(not isinstance(step,dict) for step in steps):raise Error("invalid_json")
  out.append({"job_id":x.get("id"),"run_id":x.get("run_id"),"attempt":x.get("run_attempt"),"head_sha":x.get("head_sha"),
   "name":x.get("name"),"status":x.get("status"),"conclusion":x.get("conclusion"),
   "started_at":x.get("started_at"),"completed_at":x.get("completed_at"),
   "url":x.get("html_url"),"steps":[{"number":step.get("number"),"name":step.get("name"),
   "status":step.get("status"),"conclusion":step.get("conclusion")} for step in (x.get("steps") or [])]})
 return {"schema":"gh-identity-jobs/1","repository":r,"run_id":run_id,"attempt":attempt,
  "complete":True,"count":len(out),"jobs":out,"observed_at":now()}

def _positive_identifier(value,label):
 if isinstance(value,bool) or not isinstance(value,(int,str)) or not re.fullmatch(r"[0-9]+",str(value)) or int(value)<1:
  raise ValueError("invalid "+label+" identifier")
 return int(value)

def step_url(r,run_id,job_id,step_number,line=None):
 """Build a UI permalink from explicit identities; performs no observation."""
 r=repo(r)
 run_id=_positive_identifier(run_id,"run")
 job_id=_positive_identifier(job_id,"job")
 step_number=_positive_identifier(step_number,"step")
 if line is not None:line=_positive_identifier(line,"line")
 encoded="/".join(urllib.parse.quote(part,safe="") for part in r.split("/"))
 anchor=f"#step:{step_number}"+(f":{line}" if line is not None else "")
 return f"https://github.com/{encoded}/actions/runs/{run_id}/job/{job_id}{anchor}"

def step_url_from_jobs(observation,job_id,step_number,line=None):
 """Return a link only for a unique step in a complete jobs() observation."""
 job_id=_positive_identifier(job_id,"job")
 step_number=_positive_identifier(step_number,"step")
 if line is not None:line=_positive_identifier(line,"line")
 if not isinstance(observation,dict) or observation.get("schema")!="gh-identity-jobs/1" or observation.get("complete") is not True:
  raise Error("incomplete_job_observation")
 r=repo(observation.get("repository"))
 run_id=_positive_identifier(observation.get("run_id"),"run")
 attempt=observation.get("attempt")
 if attempt is not None:attempt=_positive_identifier(attempt,"attempt")
 rows=observation.get("jobs")
 if not isinstance(rows,list):raise Error("invalid_json")
 if type(observation.get("count")) is not int or observation["count"]!=len(rows):raise Error("incomplete_job_observation")
 if any(not isinstance(row,dict) for row in rows):raise Error("invalid_json")
 matches=[row for row in rows if row.get("job_id")==job_id and type(row.get("job_id")) is int]
 if not matches:raise Error("step_not_found")
 if len(matches)!=1:raise Error("ambiguous_job_identity")
 job=matches[0]
 if type(job.get("run_id")) is not int or job.get("run_id")!=run_id:raise Error("job_identity_mismatch")
 if attempt is not None and (type(job.get("attempt")) is not int or job.get("attempt")!=attempt):raise Error("attempt_mismatch")
 steps=job.get("steps")
 if not isinstance(steps,list) or any(not isinstance(step,dict) for step in steps):raise Error("invalid_json")
 matches=[step for step in steps if step.get("number")==step_number and type(step.get("number")) is int]
 if not matches:raise Error("step_not_found")
 if len(matches)!=1:raise Error("ambiguous_step_identity")
 return step_url(r,run_id,job_id,step_number,line)

class _NoLogRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _log_header(headers, name):
    values = [value for key, value in headers.items() if key.lower() == name.lower()]
    if len(values) > 1 or any(not isinstance(value, str) for value in values):
        raise Error("invalid_log_response")
    return values[0] if values else None


def _log_redirect(current, location):
    if not isinstance(location, str) or not location:
        raise Error("invalid_log_redirect")
    if "\\" in location or any(ord(c) <= 32 or ord(c) == 127 for c in location):
        raise Error("unsafe_log_redirect")
    try:
        target = urllib.parse.urlsplit(urllib.parse.urljoin(current, location))
        host = target.hostname or ""
        allowed = host.endswith((".actions.githubusercontent.com", ".blob.core.windows.net"))
        if (target.scheme != "https" or not allowed or target.username is not None
                or target.password is not None or target.port not in (None, 443)
                or target.fragment):
            raise ValueError("redirect")
    except ValueError:
        raise Error("unsafe_log_redirect") from None
    return target.geturl()


def _normalize_job_log(raw):
    if raw.startswith((b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")):
        raise Error("unsupported_log_encoding")
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise Error("invalid_log_encoding") from None
    bom = "\ufeff" in text
    text = text.replace("\ufeff", "")
    original = text
    # Consume terminal strings, including their contents and unterminated tails.
    text = re.sub(r"(?:\x1b\]|\x9d)(?:[^\x07\x1b\x9c]|\x1b(?!\\))*(?:\x07|\x1b\\|\x9c|$)", "", text)
    text = re.sub(r"(?:\x1b[P_X^]|[\x90\x98\x9e\x9f])(?:[^\x1b\x9c]|\x1b(?!\\))*(?:\x1b\\|\x9c|$)", "", text)
    text = re.sub(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\x1b[ -/]*[@-Z\\-_]", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)
    ansi = text != original
    return text.replace("\r\n", "\n").replace("\r", "\n"), {"bom_removed": bom, "ansi_removed": ansi}


def _log_redactions(values):
    if not isinstance(values, (list, tuple)) or len(values) > 100:
        raise ValueError("redact must contain at most 100 nonempty strings")
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("redact must contain nonempty strings")
    if sum(len(value.encode("utf-8")) for value in values) > 65536:
        raise ValueError("redaction values exceed 65536 bytes")
    return tuple(values)


def _redact_job_log(text, values):
    spans = []
    masks = list(values)
    for value in re.findall(r"::add-mask::([^\r\n]+)", text, flags=re.I):
        decoded = re.sub(r"%0A|%0D|%25", lambda match: {"%0A": "\n", "%0D": "\r", "%25": "%"}[match[0].upper()], value, flags=re.I)
        masks.extend((value, decoded))
        masks.extend(decoded.split())
    # Find every mask in the same original text; a short mask must not hide a
    # token prefix or assignment key from a later, more complete rule.
    for value in set(masks):
        normalized, _ = _normalize_job_log(value.encode("utf-8"))
        if normalized:
            start = 0
            while True:
                index = text.find(normalized, start)
                if index < 0:
                    break
                spans.append((index, index + len(normalized)))
                start = index + 1
    patterns = (
        (r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----|\Z)", 0),
        (r"(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+", 0),
        (r"(?i)\bauthorization[ \t]*[:=][ \t]*(?:bearer|token|basic)[ \t]+([^\s]+)", 1),
        # Credential assignments and signed URL query parameters retain keys.
        (r"(?i)\b(?:[\w.-]*(?:token|password|passwd|secret|api[_-]?key|access[_-]?key)[\w.-]*|sig|signature|x-amz-credential|x-amz-signature)[ \t]*[=:][ \t]*(\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s&;,]+)", 1),
    )
    for pattern, group in patterns:
        spans.extend(match.span(group) for match in re.finditer(pattern, text))
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    pieces = []
    start = 0
    for left, right in merged:
        pieces.extend((text[start:left], "[REDACTED]"))
        start = right
    pieces.append(text[start:])
    return "".join(pieces), {"applied": True, "replacements": len(merged)}


def _read_job_log(spec, opener=None):
    """Private bounded byte reader; the subprocess boundary enforces wall time."""
    opener = opener or urllib.request.build_opener(_NoLogRedirect())
    deadline = time.monotonic() + spec["timeout"]
    url = spec["url"]
    headers = dict(spec["headers"])
    redirects = 0
    seen = {url}
    cap = spec["cap"]
    spec["bytes_read"] = 0
    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise Error("operation_timeout")
        return left
    def truncated(read):
        if cap < spec["max_bytes"]:
            raise Error("bytes_limit")
        return {"complete": False, "truncated": True, "text": None, "text_sha256": None,
                "bytes_read": read, "limit_bytes": spec["max_bytes"], "redirects": redirects,
                "redaction": {"applied": False, "replacements": 0},
                "normalization": {"bom_removed": False, "ansi_removed": False}}
    while True:
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            response = opener.open(req, timeout=remaining())
        except urllib.error.HTTPError as exc:
            response = exc
        except urllib.error.URLError as exc:
            code = "operation_timeout" if isinstance(exc.reason, TimeoutError) else "transport_error"
            raise Error(code) from None
        except TimeoutError:
            raise Error("operation_timeout") from None
        except (OSError, http.client.HTTPException):
            raise Error("transport_error") from None
        with contextlib.closing(response):
            remaining()
            status = response.getcode()
            rh = response.headers
            if status in (301, 302, 303, 307, 308):
                target = _log_redirect(url, _log_header(rh, "Location"))
                if redirects >= 3 or target in seen:
                    raise Error("log_redirect_limit")
                redirects += 1
                seen.add(target)
                url = target
                # A signed storage URL needs no GitHub credentials or API headers.
                headers = {"Accept": "*/*" if spec.get("binary") else "text/plain", "User-Agent": "gh_identity/0.1"}
                continue
            if status == 429 or (status == 403 and (
                    _log_header(rh, "X-RateLimit-Remaining") == "0" or _log_header(rh, "Retry-After") is not None)):
                raise Error("rate_limit")
            errors = {401: "authentication_required", 403: "permission_or_rate_limit",
                      404: "not_found_or_inaccessible", 410: "log_not_available", 206: "incomplete_log_response"}
            if status in errors:
                raise Error(errors[status])
            if status != 200:
                raise Error("server_error" if status >= 500 else "invalid_log_response")
            if _log_header(rh, "Content-Range") is not None:
                raise Error("incomplete_log_response")
            encoding = _log_header(rh, "Content-Encoding")
            if encoding is not None and encoding.lower().strip() not in ("", "identity"):
                raise Error("unsupported_log_encoding")
            content_type = _log_header(rh, "Content-Type")
            allowed_types = ("application/zip", "application/octet-stream", "application/x-zip-compressed") \
                if spec.get("binary") else ("text/plain", "application/octet-stream")
            if content_type is not None and content_type.split(";", 1)[0].strip().lower() not in allowed_types:
                raise Error("invalid_log_response")
            length = _log_header(rh, "Content-Length")
            transfer = _log_header(rh, "Transfer-Encoding")
            if transfer is not None and (transfer.lower().strip() != "chunked" or length is not None):
                raise Error("invalid_log_response")
            if length is not None:
                if not re.fullmatch(r"[0-9]{1,20}", length):
                    raise Error("invalid_log_response")
                length = int(length)
                if length > cap:
                    return truncated(0)
            raw = bytearray()
            try:
                while len(raw) <= cap:
                    remaining()
                    chunk = response.read(min(65536, cap + 1 - len(raw)))
                    remaining()
                    if not isinstance(chunk, bytes):
                        raise Error("invalid_log_response")
                    if not chunk:
                        break
                    spec["bytes_read"] += len(chunk)
                    raw.extend(chunk)
            except TimeoutError:
                raise Error("operation_timeout") from None
            except http.client.IncompleteRead as exc:
                spec["bytes_read"] += len(exc.partial)
                raise Error("incomplete_log_response") from None
            except (http.client.HTTPException, OSError):
                raise Error("incomplete_log_response") from None
            if len(raw) > cap:
                return truncated(len(raw))
            if length is not None and len(raw) != length:
                raise Error("incomplete_log_response")
            if spec.get("binary"):
                # Artifacts are opaque bytes: no text normalization or redaction.
                remaining()
                return {"complete": True, "truncated": False,
                        "data_b64": base64.b64encode(bytes(raw)).decode("ascii"),
                        "sha256": hashlib.sha256(bytes(raw)).hexdigest(),
                        "bytes_read": len(raw), "limit_bytes": spec["max_bytes"], "redirects": redirects}
            text, normalization = _normalize_job_log(bytes(raw))
            text, redaction = _redact_job_log(text, spec["redact"])
            remaining()
            return {"complete": True, "truncated": False, "text": text,
                    "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "bytes_read": len(raw), "limit_bytes": spec["max_bytes"], "redirects": redirects,
                    "normalization": normalization, "redaction": redaction}


def _job_log_worker():
    # No raw body, signed URL, credential or exception detail leaves this worker.
    spec = {}
    try:
        spec = json.load(sys.stdin)
        result = _read_job_log(spec)
    except Error as exc:
        result = {"error": exc.code, "bytes_read": spec.get("bytes_read", 0)}
    except Exception:
        result = {"error": "invalid_log_response", "bytes_read": spec.get("bytes_read", 0) if isinstance(spec, dict) else 0}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


def _job_log_credential(transport, timeout):
    value = token()
    if transport == "urllib":
        return value, "environment" if value else "anonymous"
    if transport == "gh" or (transport == "auto" and gh_available()):
        if value:
            return value, "environment"
        env = os.environ.copy()
        env.update(GH_PROMPT_DISABLED="1", GH_PAGER="cat", GH_HOST="github.com")
        env.pop("GH_DEBUG", None)
        try:
            code, raw, err = _process(["gh", "auth", "token", "--hostname", "github.com"], None, timeout, env)
        except Error as exc:
            if exc.code != "process_not_found":
                raise
            if transport == "gh":
                raise Error("gh_not_found") from None
            return None, "anonymous"
        if code:
            if code == 2:
                raise Error("cancelled")
            unavailable = code == 4 or (code == 1 and b"no oauth token found" in err.lower())
            if transport == "auto" and unavailable:
                return None, "anonymous"
            raise Error("authentication_required" if unavailable else "gh_failed")
        try:
            value = raw.decode("ascii").strip()
        except UnicodeError:
            raise Error("invalid_authentication_response") from None
        if not value or len(value) > 8192 or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise Error("invalid_authentication_response")
        return value, "gh"
    return value, "environment" if value else "anonymous"


def _download_job_log(path, *, max_bytes, redact, transport, timeout, binary=False):
    budget = _BUDGET.get()
    deadline = min(budget.deadline, time.monotonic() + timeout)
    credential, source = _job_log_credential(transport, min(timeout, budget.remaining()))
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "gh_identity/0.1"}
    if credential:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,8192}", credential):
            raise Error("invalid_authentication_response")
        headers["Authorization"] = "Bearer " + credential
    values = list(redact) + [value for value in (credential, os.getenv("GH_TOKEN"), os.getenv("GITHUB_TOKEN")) if value]
    left = min(budget.remaining(), deadline - time.monotonic())
    if left <= 0:
        budget.fail("operation_timeout")
    available = budget.max_bytes - budget.bytes
    if available <= 0:
        budget.fail("bytes_limit")
    spec = {"url": API + "/" + path, "headers": headers, "timeout": left,
            "cap": min(max_bytes, available), "max_bytes": max_bytes, "redact": values, "binary": binary}
    code, raw, err = _process([sys.executable, os.path.abspath(__file__), "--_job-log-worker"],
                              json.dumps(spec).encode(), left)
    if code:
        raise Error("transport_error")
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise Error("invalid_log_response") from None
    if not isinstance(result, dict):
        raise Error("invalid_log_response")
    if type(result.get("bytes_read")) is not int or result["bytes_read"] < 0:
        raise Error("invalid_log_response")
    # Charge received bytes on failures too; callers cannot reset a shared
    # operation's byte budget by catching decode/framing/redaction errors.
    budget.charge("bytes", max(0, result["bytes_read"] - len(raw) - len(err)))
    if "error" in result:
        if result["error"] in ("bytes_limit", "operation_timeout"):
            budget.fail(result["error"])
        raise Error(result["error"])
    return {**result, "transport": "urllib", "credential_source": source}


def job_log(r, run_id, job_id, *, attempt, max_bytes=1_000_000, redact=(), transport="auto", timeout=30):
    """Read a completed job's complete, redacted log under exact attempt identity.

    A local byte cap returns truncated=True and text=None; protocol truncation
    and operation budget exhaustion raise Error. No prefix or raw log is saved.
    """
    r = repo(r)
    run_id = _positive_identifier(run_id, "run")
    job_id = _positive_identifier(job_id, "job")
    attempt = _positive_identifier(attempt, "attempt")
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    if transport not in ("auto", "gh", "urllib"):
        raise ValueError("bad transport")
    redact = _log_redactions(redact)
    budget = _BUDGET.get()
    deadline = min(budget.deadline, time.monotonic() + timeout)
    def remaining():
        left = min(budget.remaining(), deadline - time.monotonic())
        if left <= 0:
            budget.fail("operation_timeout")
        return left
    observed_run = run(r, run_id, attempt=attempt, transport=transport, timeout=remaining())
    sha = observed_run.get("head_sha")
    if (type(observed_run.get("run_id")) is not int or observed_run["run_id"] != run_id
            or type(observed_run.get("attempt")) is not int or observed_run["attempt"] != attempt
            or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha)):
        raise Error("invalid_run_identity")
    observation = jobs(r, run_id, attempt=attempt, transport=transport, timeout=remaining())
    selected = [job for job in observation["jobs"] if job["job_id"] == job_id]
    if not selected:
        raise Error("job_not_found")
    job = selected[0]
    if not isinstance(job.get("head_sha"), str) or job["head_sha"].lower() != sha.lower():
        raise Error("job_identity_mismatch")
    if job["status"] != "completed":
        raise Error("job_not_completed")
    step_urls = []
    for step in job["steps"]:
        if type(step["number"]) is not int or step["number"] < 1:
            raise Error("invalid_step_identity")
        step_urls.append({"number": step["number"], "url": step_url_from_jobs(observation, job_id, step["number"])})
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in r.split("/"))
    result = _download_job_log(f"repos/{encoded}/actions/jobs/{job_id}/logs", max_bytes=max_bytes,
                               redact=redact, transport=transport, timeout=remaining())
    remaining()
    return {"schema": "gh-identity-job-log/1", "repository": r, "run_id": run_id,
            "job_id": job_id, "attempt": attempt, "head_sha": sha.lower(), "step_urls": step_urls,
            **result, "observed_at": now()}


def artifacts(r, run_id, *, max_items=100, max_pages=10, transport="auto", timeout=30):
    """List one run's artifacts; every row must belong to that run."""
    r = repo(r)
    run_id = _positive_identifier(run_id, "run")
    rows = []; page = 1; exhausted = False; total = None
    while page <= max_pages and len(rows) < max_items:
        data, _headers = _page(f"repos/{r}/actions/runs/{run_id}/artifacts?per_page=100&page={page}",
                               transport=transport, timeout=timeout)
        if not isinstance(data, dict) or not isinstance(data.get("artifacts"), list):
            raise Error("invalid_json")
        total = data.get("total_count") if total is None else total
        batch = data["artifacts"]
        _BUDGET.get().charge("items", len(batch))
        for item in batch:
            owner = (item.get("workflow_run") or {}) if isinstance(item, dict) else {}
            if type(item.get("id")) is not int or owner.get("id") != run_id:
                raise Error("artifact_identity_mismatch")
            rows.append({"artifact_id": item["id"], "name": item.get("name"),
                         "size_in_bytes": item.get("size_in_bytes"), "expired": item.get("expired"),
                         "digest": item.get("digest"), "created_at": item.get("created_at"),
                         "head_sha": owner.get("head_sha")})
        if len(batch) < 100:
            exhausted = True
            break
        page += 1
    complete = exhausted and (type(total) is not int or total == len(rows))
    return {"schema": "gh-identity-artifacts/1", "repository": r, "run_id": run_id, "artifacts": rows,
            "count": len(rows), "complete": complete, "truncated": not complete, "observed_at": now()}


def artifact_file(r, artifact_id, member, *, max_bytes=10_000_000, transport="auto", timeout=30):
    """Read one member of an artifact zip under a byte cap, with its SHA-256.

    The zip is fetched through the bounded download worker (allow-listed
    redirects, no credentials sent to storage); the member's declared and
    actual sizes must both fit the cap, so a zip bomb cannot expand past it.
    """
    r = repo(r)
    artifact_id = _positive_identifier(artifact_id, "artifact")
    if not isinstance(member, str) or not member or member.startswith("/") or ".." in member.split("/"):
        raise ValueError("invalid member")
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    meta = request("GET", f"repos/{r}/actions/artifacts/{artifact_id}", transport=transport, timeout=timeout)
    if not isinstance(meta, dict) or meta.get("id") != artifact_id:
        raise Error("artifact_identity_mismatch")
    if meta.get("expired"):
        raise Error("artifact_expired")
    owner = meta.get("workflow_run") or {}
    base = {"schema": "gh-identity-artifact-file/1", "repository": r, "artifact_id": artifact_id,
            "name": meta.get("name"), "run_id": owner.get("id"), "head_sha": owner.get("head_sha"),
            "member": member}
    got = _download_job_log(f"repos/{r}/actions/artifacts/{artifact_id}/zip", max_bytes=max_bytes,
                            redact=(), transport=transport, timeout=timeout, binary=True)
    if not got.get("complete"):
        return {**base, "complete": False, "truncated": True, "text": None, "sha256": None,
                "bytes_read": got.get("bytes_read"), "observed_at": now()}
    try:
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(got["data_b64"]))) as archive:
            infos = [info for info in archive.infolist() if info.filename == member]
            if len(infos) != 1:
                raise Error("artifact_member_not_found")
            if infos[0].file_size > max_bytes:
                return {**base, "complete": False, "truncated": True, "text": None, "sha256": None,
                        "zip_sha256": got["sha256"], "observed_at": now()}
            with archive.open(infos[0]) as stream:
                data = stream.read(max_bytes + 1)
    except (zipfile.BadZipFile, ValueError, OSError):
        raise Error("invalid_artifact_archive") from None
    if len(data) > max_bytes:
        raise Error("invalid_artifact_archive")
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        text = None
    return {**base, "complete": True, "truncated": False, "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "zip_sha256": got["sha256"],
            "text": text, "observed_at": now()}


def _log_redact_env(names):
    values = []
    for name in names:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or not os.getenv(name):
            raise ValueError("redaction environment variable must be set and nonempty")
        values.append(os.environ[name])
    return values


def gh_help(*parts,timeout=15):
 if not gh_available():raise Error("gh_not_found")
 env=os.environ.copy();env.update(GH_PROMPT_DISABLED="1",GH_PAGER="cat");env.pop("GH_REPO",None)
 try:p=subprocess.run(["gh","help",*parts],capture_output=True,text=True,encoding="utf-8",timeout=timeout,env=env)
 except OSError as e:raise Error("process_error") from e
 if p.returncode:raise Error("gh_failed")
 return p.stdout

def local_identity(*,cwd=None,identity=None,env=None):
 env=os.environ if env is None else env
 if identity is not None:
  sha=identity.get("sha")
  if sha is not None and not re.fullmatch(r"[0-9a-fA-F]{40}",str(sha)):raise ValueError("invalid local sha")
  return {"schema":"gh-identity-local/1","sha":str(sha).lower() if sha else None,"ref":identity.get("ref"),"dirty":identity.get("dirty"),"source":identity.get("source","provided"),"observed_at":now()}
 sha=env.get("GITHUB_SHA")
 ref=env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF_NAME")
 if sha and re.fullmatch(r"[0-9a-fA-F]{40}",sha):
  return {"schema":"gh-identity-local/1","sha":sha.lower(),"ref":ref,"dirty":None,"source":"github-env","observed_at":now()}
 if shutil.which("git") is None:
  return {"schema":"gh-identity-local/1","sha":None,"ref":None,"dirty":None,"source":"unavailable","observed_at":now()}
 root=cwd or os.getcwd()
 def git(*args):
  p=subprocess.run(["git",*args],cwd=root,capture_output=True,text=True,encoding="utf-8",check=False)
  if p.returncode:raise Error("git_failed")
  return p.stdout.strip()
 sha=git("rev-parse","HEAD")
 if not re.fullmatch(r"[0-9a-fA-F]{40}",sha):raise Error("invalid_commit")
 ref=git("branch","--show-current") or None
 dirty=bool(git("status","--porcelain"))
 return {"schema":"gh-identity-local/1","sha":sha.lower(),"ref":ref,"dirty":dirty,"source":"git","observed_at":now()}

def compare_sha(local,remote_sha):
 lsha=local.get("sha") if isinstance(local,dict) else None
 lsha=str(lsha).lower() if lsha else None
 rsha=str(remote_sha).lower() if remote_sha else None
 return {"schema":"gh-identity-comparison/1","local_sha":lsha,"remote_sha":rsha,"comparable":bool(lsha and rsha),"same":(lsha==rsha) if lsha and rsha else None,"observed_at":now()}

# Local Git observations migrated from parent git_inspector.py (380d877).
# Named read-only operations, bounded output; trusted checkout configuration.
from pathlib import Path, PurePosixPath

class GitInspectionError(RuntimeError):
    """A bounded read-only Git observation failed."""


def _git_positive(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(name + " must be a positive integer")
    return value


def _git_root(root):
    path = Path(root)
    if not path.exists():
        raise ValueError("root does not exist")
    return path


def _git_path(value):
    if not isinstance(value, (str, os.PathLike)):
        raise TypeError("path must be string or path-like")
    value = os.fspath(value)
    if not value or "\x00" in value:
        raise ValueError("path must be non-empty and contain no NUL")
    return value


def _git_ref(value):
    if not isinstance(value, str) or not value or "\x00" in value or value.startswith("-"):
        raise ValueError("revision must be a non-option string without NUL")
    return value


def _git_spawn(command, **kwargs):
    return subprocess.Popen(command, **kwargs)


def _git_drain_bounded(stream, max_bytes, result):
    """Drain one child pipe fully while retaining at most max_bytes bytes."""
    kept = bytearray()
    truncated = False
    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            room = max_bytes - len(kept)
            if room > 0:
                kept.extend(chunk[:room])
            if len(chunk) > max(room, 0):
                truncated = True
    finally:
        stream.close()
    result.append((bytes(kept), truncated))


def _git_run(root, args, *, max_bytes=1_000_000, ok=(0,), input_bytes=None, timeout=None,
             inherit_auth=False, _auth_read=False, _auth_file=None, _auth_file_query=None):
    _git_positive(max_bytes, "max_bytes")
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("timeout must be positive and finite")
    if input_bytes is not None and not isinstance(input_bytes, bytes):
        raise TypeError("input_bytes must be bytes")
    if inherit_auth:
        # Read configuration only; Git parses includes/conditional includes but
        # does not execute helpers here. Never expose this private snapshot.
        deadline = time.monotonic() + (timeout if timeout is not None else 120)
        raw, truncated, _ = _git_run(
            root, ["config", "--includes", "--null", "--show-scope", "--list"],
            max_bytes=max_bytes, timeout=max(0.001, deadline-time.monotonic()), _auth_read=True)
        if truncated:
            raise GitInspectionError("Git authentication configuration exceeds output limit")
        fields = raw.split(b"\0")
        entries = []
        for scope, record in zip(fields[0::2], fields[1::2]):
            if scope not in (b"system", b"global"):
                continue
            key, sep, value = record.partition(b"\n")
            name = key.decode("utf-8", "surrogateescape")
            lower = name.lower()
            if lower == "core.sshcommand" or (lower.startswith("credential.") and
                    lower.rsplit(".", 1)[-1] in ("helper", "username", "usehttppath")):
                entries.append((name, value.decode("utf-8", "surrogateescape") if sep else "true"))
        def quoted(value):
            return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\t', '\\t').replace('\b', '\\b') + '"'
        with tempfile.TemporaryDirectory(prefix="ghi-auth-") as directory:
            filename = os.path.join(directory, "config")
            with open(filename, "w", encoding="utf-8", errors="surrogateescape") as stream:
                for name, value in entries:
                    section, rest = name.split(".", 1)
                    if "." in rest:
                        subsection, variable = rest.rsplit(".", 1)
                        stream.write("[" + section + " " + quoted(subsection) + "]\n")
                    else:
                        variable = rest
                        stream.write("[" + section + "]\n")
                    stream.write(variable + " = " + quoted(value) + "\n")
            os.chmod(filename, 0o600)
            return _git_run(root, args, max_bytes=max_bytes, ok=ok, input_bytes=input_bytes,
                            timeout=max(0.001, deadline-time.monotonic()), _auth_file=filename)
    command = ["git", "-C", str(_git_root(root)), "--no-pager",
               "--no-optional-locks", "-c", "core.fsmonitor=false",
               "-c", "core.askPass=", "-c", "credential.interactive=false", *args]
    env = os.environ.copy()
    for name in list(env):
        if name.startswith("GIT_TRACE") or name == "GIT_CURL_VERBOSE":
            env.pop(name)
    # Do not let inherited Git process-routing/config overrides redirect a
    # supposedly local inspection to another worktree/index/object database or
    # inject an external diff helper. Ordinary locale/identity variables are
    # harmless observations and remain untouched.
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                 "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_PREFIX",
                 "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                 "GIT_GRAFT_FILE", "GIT_SHALLOW_FILE",
                 "GIT_REPLACE_REF_BASE", "GIT_NO_REPLACE_OBJECTS",
                 "GIT_EXTERNAL_DIFF", "GIT_DIFF_OPTS",
                 "GIT_CONFIG", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
                 "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
                 "GIT_CONFIG_NOSYSTEM"):
        env.pop(name, None)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    # Never wait for a person: a network command without cached credentials must
    # fail fast instead of blocking on a username/password or SSH prompt.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env["GIT_ASKPASS"] = ""
    env["SSH_ASKPASS"] = ""
    env["SSH_ASKPASS_REQUIRE"] = "never"
    if _auth_read:
        # Only the fixed config query above uses ambient config paths.
        for name in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM"):
            if name in os.environ:
                env[name] = os.environ[name]
    elif _auth_file is not None:
        deadline = time.monotonic() + timeout
        ssh, truncated, _ = _git_run(root, ["config", "--get", "core.sshCommand"],
                                    max_bytes=max_bytes, ok=(0, 1), timeout=timeout, _auth_file_query=_auth_file)
        if truncated:
            raise GitInspectionError("SSH configuration exceeds output limit")
        if "GIT_SSH_COMMAND" not in env and not ssh and "GIT_SSH" not in env:
            env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
        timeout = max(0.001, deadline-time.monotonic())
    elif _auth_file_query is None:
        env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    if not _auth_read:
        env["GIT_CONFIG_GLOBAL"] = _auth_file or _auth_file_query or os.devnull
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_NOSYSTEM"] = "1"
    try:
        proc = _git_spawn(
            command,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=env,
            # Own process group, so a timeout can also stop transport helpers
            # (git-remote-https, ssh, upload-pack) that hold the output pipes.
            **({"start_new_session": True} if timeout is not None and os.name == "posix" else {}),
        )
    except FileNotFoundError as error:
        raise GitInspectionError("git executable not found") from error
    except OSError as error:
        raise GitInspectionError(type(error).__name__) from error

    stdout_result = []
    stderr_result = []
    stdout_thread = threading.Thread(
        target=_git_drain_bounded, args=(proc.stdout, max_bytes, stdout_result),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_git_drain_bounded, args=(proc.stderr, max_bytes, stderr_result),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    if input_bytes is not None:
        try:
            proc.stdin.write(input_bytes)
            proc.stdin.close()
        except BrokenPipeError:
            pass
    try:
        returncode = proc.wait() if timeout is None else proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait()
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        raise GitInspectionError(f"git timed out after {timeout:g}s")
    stdout_thread.join()
    stderr_thread.join()

    raw, truncated = stdout_result[0]
    # stderr is deliberately drained and bounded even though the public
    # contract does not expose command stderr.
    _stderr, _stderr_truncated = stderr_result[0]
    if returncode not in ok:
        raise GitInspectionError("git exited with status " + str(returncode))
    return raw, truncated, returncode

def _git_decode(raw):
    # Results are JSON-compatible observations. Invalid or byte-truncated UTF-8
    # is made explicit as U+FFFD instead of leaking lone surrogate code points.
    return raw.decode("utf-8", "replace")


def _git_complete_fields(raw, delimiter, truncated):
    """Drop a byte-truncated trailing field instead of publishing corruption."""
    if truncated and not raw.endswith(delimiter):
        boundary = raw.rfind(delimiter)
        raw = b"" if boundary < 0 else raw[:boundary + len(delimiter)]
    fields = raw.split(delimiter)
    if fields and fields[-1] == b"":
        fields.pop()
    return fields


def git_status(root=".", *, max_bytes=1_000_000):
    """Return structured porcelain-v2 status without inventing identity."""
    raw, truncated, _ = _git_run(
        root, ["status", "--porcelain=v2", "-z", "--untracked-files=all"],
        max_bytes=max_bytes,
    )
    fields = _git_complete_fields(raw, b"\0", truncated)
    records = []
    index = 0
    while index < len(fields):
        text = _git_decode(fields[index])
        kind = text[:1]
        if kind == "2":
            parts = text.split(" ", 9)
            if len(parts) != 10 or index + 1 >= len(fields):
                if truncated:
                    break
                raise GitInspectionError("malformed porcelain-v2 type-2 record")
            records.append({"kind": "2", "record": text, "path": parts[9],
                            "orig_path": _git_decode(fields[index + 1])})
            index += 2
            continue
        if kind == "1":
            parts = text.split(" ", 8)
            path = parts[8] if len(parts) == 9 else None
        elif kind == "u":
            parts = text.split(" ", 10)
            path = parts[10] if len(parts) == 11 else None
        elif kind in ("?", "!") and text.startswith(kind + " "):
            path = text[2:]
        else:
            path = None
        records.append({"kind": kind, "record": text, "path": path})
        index += 1
    return {"clean": not records and not truncated, "records": records, "truncated": truncated}

def render_git_status(o):
    """Short XY/path display from the existing bounded porcelain-v2 records."""
    def path(value):
        value = value or ""
        return json.dumps(value, ensure_ascii=False) if any(c.isspace() or c in '\\"' or ord(c)<32 or ord(c)==127 for c in value) else value
    lines = []
    for row in o["records"]:
        kind = row["kind"]
        xy = {"?": "??", "!": "!!"}.get(kind)
        if xy is None:
            parts = row["record"].split(" ", 2)
            xy = parts[1].replace(".", " ") if len(parts)>1 else "??"
        name = path(row["path"])
        if kind == "2":
            name = path(row["orig_path"]) + " -> " + name
        lines.append(xy + " " + name)
    if o["truncated"]:
        lines.append("(truncated)")
    return "\n".join(lines)

def _git_head(root):
    """Current branch name ("" when detached) and HEAD commit."""
    branch = _git_decode(_git_run(root, ["branch", "--show-current"])[0]).strip()
    sha = _git_decode(_git_run(root, ["rev-parse", "--verify", "HEAD"])[0]).strip()
    return {"branch": branch, "head_sha": sha}


def _git_branch_exists(root, name):
    return _git_run(root, ["show-ref", "--verify", "--quiet", "refs/heads/" + name], ok=(0, 1))[2] == 0


def git_switch(root=".", branch=None, *, create=False, previous=False, write=False):
    """Plan, or with write=True perform, one `git switch` and verify the result.

    Exactly one of: switch to an existing local branch, create a new branch
    (`-c`), or return to the previous branch (`-`). Without write nothing is
    changed. Existing-branch switches use --no-guess so a remote-tracking
    branch is never created implicitly.
    """
    if previous == (branch is not None):
        raise ValueError("give a branch name or previous=True, not both")
    if create and previous:
        raise ValueError("create cannot be combined with previous")
    before = _git_head(root)
    out = {"schema": "gh-identity-git-switch/1", "status": "planned", "mutation_status": "not_attempted",
           "verification_status": "not_attempted", "before": before}
    if previous:
        target = _git_decode(_git_run(root, ["rev-parse", "--abbrev-ref", "--verify", "@{-1}"],
                                      ok=(0, 128))[0]).strip()
        if not target:
            # Empty when there is no previous position or it was a detached
            # HEAD; `git switch -` itself refuses a commit there.
            raise GitInspectionError("previous position is not a branch")
        args = ["switch", "-"]
    else:
        name = _git_ref(branch)
        # Validate as a branch name without touching refs.
        if _git_run(root, ["check-ref-format", "--branch", name], ok=(0, 1, 128))[2] != 0:
            raise ValueError("invalid branch name")
        exists = _git_branch_exists(root, name)
        if create and exists:
            raise GitInspectionError("branch already exists")
        if not create and not exists:
            raise GitInspectionError("local branch not found")
        target = name
        args = ["switch", "-c", name] if create else ["switch", "--no-guess", name]
    out.update(target=target, command=["git", *args])
    if not write:
        return out
    try:
        _git_run(root, args)
    except GitInspectionError as error:
        out.update(status="mutation_failed", mutation_status="failed", error=str(error))
        return out
    out["mutation_status"] = "succeeded"
    after = _git_head(root)
    out["after"] = after
    verified = after["branch"] == target
    if create:
        verified = verified and after["head_sha"] == before["head_sha"]
    out.update(verified=verified, verification_status="verified" if verified else "mismatch",
               status="verified" if verified else "verification_mismatch")
    return out


def _git_config_get(root, key):
    out, _, code = _git_run(root, ["config", "--local", "--get", key], ok=(0, 1))
    return _git_decode(out).strip() if code == 0 else None


def git_work(root=".", source=None, branch=None, *, upstream=None, remote="origin", depth=None, fetch_timeout=120,
             write=False):
    """Plan, or with write=True perform, "start a work branch from a PR head or remote branch".

    The steps are the ones a shallow single-branch clone needs, where
    `git checkout -t` / `git branch -u` fail because the remote-tracking ref
    does not exist or is not covered by the fetch refspec:

    1. fetch the source into an explicit remote-tracking ref
       (PR N -> refs/remotes/REMOTE/pr/N, branch B -> refs/remotes/REMOTE/B);
    2. `git switch -c BRANCH` at that ref (an existing BRANCH is refused, never reset);
    3. write branch.BRANCH.remote and branch.BRANCH.merge directly, so a plain
       `git push` / `git status -sb` uses UPSTREAM (default: BRANCH) even before
       that remote branch exists, and add a fetch refspec for UPSTREAM when the
       clone's refspec does not cover it (single-branch clones).

    The fetch disables Git/GCM/askpass prompts (default SSH uses BatchMode;
    configured SSH wrappers must implement their own noninteractive behavior) and is killed after
    fetch_timeout seconds, so a missing credential or a stalled transfer ends as
    mutation_failed with nothing switched, instead of hanging.
    Verification reads back the current branch, HEAD == fetched commit, and both config values.
    In a shallow clone the fetch is shallow too (depth 1 unless given), so history is not
    silently deepened.
    """
    if source is None or branch is None:
        raise ValueError("source and branch are required")
    name = _git_ref(branch)
    up = _git_ref(upstream if upstream is not None else branch)
    remote = _git_ref(remote)
    for candidate in (name, up):
        if _git_run(root, ["check-ref-format", "--branch", candidate], ok=(0, 1, 128))[2] != 0:
            raise ValueError("invalid branch name")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", remote):
        raise ValueError("invalid remote name")
    if _git_config_get(root, f"remote.{remote}.url") is None:
        raise GitInspectionError("remote not found")
    text = str(source)
    match = re.fullmatch(r"(?:#|pr/|pull/)?([1-9][0-9]{0,8})", text)
    if match:
        number = int(match.group(1))
        src, tracking = f"refs/pull/{number}/head", f"refs/remotes/{remote}/pr/{number}"
        kind = "pull_request"
    else:
        src_branch = _git_ref(text.removeprefix("refs/heads/"))
        if _git_run(root, ["check-ref-format", "--branch", src_branch], ok=(0, 1, 128))[2] != 0:
            raise ValueError("invalid source branch")
        src, tracking, kind = f"refs/heads/{src_branch}", f"refs/remotes/{remote}/{src_branch}", "branch"
    if _git_branch_exists(root, name):
        raise GitInspectionError("branch already exists")
    shallow = _git_decode(_git_run(root, ["rev-parse", "--is-shallow-repository"])[0]).strip() == "true"
    if depth is None and shallow:
        depth = 1
    if depth is not None and (isinstance(depth, bool) or not isinstance(depth, int) or depth < 1):
        raise ValueError("depth must be a positive integer")
    if isinstance(fetch_timeout, bool) or not isinstance(fetch_timeout, (int, float)) or not math.isfinite(fetch_timeout) or fetch_timeout <= 0:
        raise ValueError("fetch_timeout must be positive and finite")
    fetch = ["fetch", "--no-tags", *(["--depth", str(depth)] if depth else []), remote, f"+{src}:{tracking}"]
    switch = ["switch", "-c", name, "--no-track", tracking]
    config = [["config", "--local", f"branch.{name}.remote", remote],
              ["config", "--local", f"branch.{name}.merge", f"refs/heads/{up}"]]
    # A single-branch clone's fetch refspec maps only its one branch, so Git finds no
    # remote-tracking ref for UPSTREAM: `status -sb` shows no pair and @{u} fails even
    # after a push. Add a refspec for exactly that branch unless one already covers it.
    refspec = f"+refs/heads/{up}:refs/remotes/{remote}/{up}"
    existing = _git_decode(_git_run(root, ["config", "--local", "--get-all", f"remote.{remote}.fetch"],
                                    ok=(0, 1))[0]).split()
    covered = refspec in existing or f"+refs/heads/*:refs/remotes/{remote}/*" in existing
    if not covered:
        config.append(["config", "--local", "--add", f"remote.{remote}.fetch", refspec])
    before = _git_head(root)
    out = {"schema": "gh-identity-git-work/1", "status": "planned", "mutation_status": "not_attempted",
           "verification_status": "not_attempted", "before": before, "source": {"kind": kind, "ref": src},
           "tracking_ref": tracking, "branch": name, "upstream": {"remote": remote, "merge": f"refs/heads/{up}"},
           "shallow": shallow, "refspec_added": None if covered else refspec, "commands": [["git", *c] for c in (fetch, switch, *config)]}
    if not write:
        return out
    done = []
    try:
        for step in (fetch, switch, *config):
            _git_run(root, step, timeout=fetch_timeout if step is fetch else None,
                     inherit_auth=step is fetch)
            done.append(step[0])
    except GitInspectionError as error:
        out.update(status="mutation_failed", mutation_status="partial" if done else "failed",
                   completed_steps=done, error=str(error))
        return out
    out["mutation_status"] = "succeeded"
    fetched = _git_decode(_git_run(root, ["rev-parse", "--verify", tracking + "^{commit}"])[0]).strip()
    after = _git_head(root)
    got = {"remote": _git_config_get(root, f"branch.{name}.remote"),
           "merge": _git_config_get(root, f"branch.{name}.merge")}
    out.update(after=after, fetched_sha=fetched, upstream_config=got)
    verified = after["branch"] == name and after["head_sha"] == fetched and got == out["upstream"]
    out.update(verified=verified, verification_status="verified" if verified else "mismatch",
               status="verified" if verified else "verification_mismatch")
    return out


def git_ls_files(root=".", *, include_untracked=False, max_files=10_000,
             max_bytes=1_000_000):
    """Return a bounded NUL-safe file inventory.

    The default is tracked-only. include_untracked=True additionally observes
    untracked-but-not-ignored entries without weakening existing consumers.
    Combined mode preserves Git's output order; a truncated prefix is not
    guaranteed to contain any tracked path. Nested untracked repositories may
    appear as directory entries, and tracked paths may be absent in the worktree.
    """
    if not isinstance(include_untracked, bool):
        raise TypeError("include_untracked must be bool")
    _git_positive(max_files, "max_files")
    args = ["ls-files", "-z"]
    if include_untracked:
        args.extend(["--cached", "--others", "--exclude-standard"])
    raw, byte_truncated, _ = _git_run(root, args, max_bytes=max_bytes)
    paths = [_git_decode(item) for item in _git_complete_fields(raw, b"\0", byte_truncated)]
    record_truncated = len(paths) > max_files
    return {"paths": paths[:max_files],
            "truncated": byte_truncated or record_truncated}


def git_diff(root=".", *, staged=False, base=None, head=None, path=None,
         max_bytes=1_000_000):
    """Return a bounded patch with external diff/textconv disabled."""
    args = ["-c", "diff.external=", "diff", "--no-ext-diff", "--no-textconv",
            "--no-color"]
    if staged:
        if base is not None or head is not None:
            raise ValueError("staged diff cannot also specify revisions")
        args.append("--cached")
    elif base is not None:
        args.append(_git_ref(base))
        if head is not None:
            args.append(_git_ref(head))
    elif head is not None:
        raise ValueError("head requires base")
    args.append("--")
    if path is not None:
        args.append(_git_path(path))
    raw, truncated, _ = _git_run(root, args, max_bytes=max_bytes)
    return {"patch": _git_decode(raw), "truncated": truncated}


def git_log(root=".", *, max_count=50, path=None, max_bytes=1_000_000):
    """Return bounded commit observations; not canonical repository metadata."""
    _git_positive(max_count, "max_count")
    fmt = "%H%x1f%aI%x1f%an%x1f%s%x1e"
    args = ["log", "--no-decorate", "--no-color", "--format=" + fmt,
            "--max-count=" + str(max_count)]
    if path is not None:
        args.extend(["--", _git_path(path)])
    raw, truncated, _ = _git_run(root, args, max_bytes=max_bytes)
    rows = []
    for record in _git_complete_fields(raw, b"\x1e", truncated):
        record = record.strip(b"\r\n")
        if not record:
            continue
        fields = _git_decode(record).split("\x1f")
        if len(fields) == 4:
            rows.append(dict(zip(("commit", "authored_at", "author", "subject"),
                                 fields)))
    return {"commits": rows, "truncated": truncated}


def git_log_numstat(root=".", *, since=None, max_count=10_000,
                max_bytes=1_000_000):
    """Return bounded per-commit file churn using NUL-safe numstat output.

    Dates are committer dates (Git %cs), matching repo_overview's existing
    display. Binary counts are None. Renames retain both paths. Git's usual
    history/merge/rename semantics are preserved. A byte-truncated final commit
    is omitted in full; truncated never masquerades as complete history.
    """
    _git_positive(max_count, "max_count")
    if since is not None:
        if not isinstance(since, str):
            raise TypeError("since must be a string or None")
        if not since or "\x00" in since:
            raise ValueError("since must be non-empty without NUL")
    args = ["log", "--no-ext-diff", "--no-textconv", "--no-color",
            "-z", "--numstat", "--format=%x00%H%x00%cs",
            "--max-count=" + str(max_count + 1)]
    if since is not None:
        args.append("--since=" + since)
    args.append("--")
    raw, byte_truncated, _ = _git_run(root, args, max_bytes=max_bytes)
    fields = _git_complete_fields(raw, b"\0", byte_truncated)
    commits = []
    current = None
    index = 0
    while index < len(fields):
        field = fields[index]
        if field == b"":
            if current is not None:
                commits.append(current)
                current = None
            if index + 2 >= len(fields):
                if byte_truncated:
                    break
                raise GitInspectionError("incomplete numstat commit header")
            sha, date = fields[index + 1:index + 3]
            if (len(sha) not in (40, 64) or any(c not in b"0123456789abcdef" for c in sha)
                    or len(date) != 10 or date[4:5] != b"-" or date[7:8] != b"-"
                    or not date.replace(b"-", b"").isdigit()):
                raise GitInspectionError("malformed numstat commit header")
            current = {"commit": _git_decode(sha), "date": _git_decode(date), "files": []}
            index += 3
            continue
        if current is None:
            raise GitInspectionError("numstat record without commit")
        # Git separates the header from stats with a newline. Split only the
        # two count separators; tabs/newlines inside the filename are data.
        parts = field.lstrip(b"\n").split(b"\t", 2)
        if len(parts) != 3:
            raise GitInspectionError("malformed numstat file record")
        added, deleted, path = parts
        if (added == b"-") != (deleted == b"-") or any(
                count != b"-" and not count.isdigit() for count in (added, deleted)):
            raise GitInspectionError("malformed numstat counts")
        orig_path = None
        if not path:
            if index + 2 >= len(fields):
                if byte_truncated:
                    current = None
                    break
                raise GitInspectionError("incomplete numstat rename")
            orig_path, path = fields[index + 1:index + 3]
            if not orig_path or not path:
                raise GitInspectionError("empty numstat rename path")
            index += 2
        current["files"].append({
            "path": _git_decode(path),
            "orig_path": _git_decode(orig_path) if orig_path is not None else None,
            "added": None if added == b"-" else int(added),
            "deleted": None if deleted == b"-" else int(deleted),
        })
        index += 1
    if current is not None and not byte_truncated:
        commits.append(current)
    return {"commits": commits[:max_count],
            "truncated": byte_truncated or len(commits) > max_count}


def git_show(root=".", revision="HEAD", *, path=None, max_bytes=1_000_000):
    """Show one revision/path with bounded output and no external textconv."""
    spec = _git_ref(revision)
    if path is not None:
        # The path is encoded in the revision:path object expression.
        # --end-of-options protects revision parsing from option-like specs.
        spec += ":" + _git_path(path)
    raw, truncated, _ = _git_run(
        root, ["-c", "diff.external=", "show", "--no-ext-diff", "--no-textconv",
               "--no-color", "--end-of-options", spec],
        max_bytes=max_bytes,
    )
    return {"content": _git_decode(raw), "truncated": truncated}


def git_blame(root=".", path=None, *, revision="HEAD", start=None, end=None,
          max_bytes=1_000_000):
    """Return bounded line-porcelain blame for one explicit path."""
    if path is None:
        raise ValueError("path is required")
    args = ["blame", "--line-porcelain"]
    if start is not None or end is not None:
        if start is None or end is None:
            raise ValueError("start and end must be supplied together")
        _git_positive(start, "start")
        _git_positive(end, "end")
        if end < start:
            raise ValueError("end must be >= start")
        args.extend(["-L", f"{start},{end}"])
    args.extend([_git_ref(revision), "--", _git_path(path)])
    raw, truncated, _ = _git_run(root, args, max_bytes=max_bytes)
    return {"porcelain": _git_decode(raw), "truncated": truncated}


def git_grep(root=".", pattern=None, *, max_bytes=1_000_000):
    """Return NUL-safe tracked filenames containing a fixed literal pattern.

    Matched line text is deliberately omitted so newline-containing filenames
    cannot become ambiguous with content records.
    """
    if not isinstance(pattern, str) or not pattern or "\x00" in pattern:
        raise ValueError("pattern must be a non-empty string without NUL")
    raw, truncated, code = _git_run(
        root, ["grep", "-z", "-l", "-I", "-F", "-e", pattern, "--"],
        max_bytes=max_bytes, ok=(0, 1),
    )
    paths = [] if code == 1 else [
        _git_decode(item) for item in _git_complete_fields(raw, b"\0", truncated)]
    return {"paths": paths, "truncated": truncated}

def git_check_ignore(root=".", paths=(), *, max_paths=1000, max_bytes=1_000_000):
    """Explain ignore state using bounded NUL-safe stdin and verbose output."""
    if isinstance(paths, (str, os.PathLike)):
        raise TypeError("paths must be a sequence")
    _git_positive(max_paths, "max_paths")
    paths = tuple(_git_path(p) for p in paths)
    if len(paths) > max_paths:
        raise ValueError("too many paths")
    if not paths:
        return {"records": [], "truncated": False}
    payload = b"\0".join(os.fsencode(p) for p in paths) + b"\0"
    if len(payload) > max_bytes:
        raise ValueError("path input exceeds byte limit")
    raw, truncated, _ = _git_run(
        root, ["check-ignore", "-z", "-v", "--no-index", "--stdin"],
        max_bytes=max_bytes, ok=(0, 1), input_bytes=payload,
    )
    fields = _git_complete_fields(raw, b"\0", truncated)
    if not truncated and len(fields) % 4:
        raise GitInspectionError("malformed check-ignore output")
    records = []
    for index in range(0, len(fields) - 3, 4):
        source, line, pattern, path = fields[index:index + 4]
        pattern_text = _git_decode(pattern)
        records.append({
            "path": _git_decode(path),
            "source": _git_decode(source),
            "line": int(line or b"0"),
            "pattern": pattern_text,
            "status": "not_ignored" if pattern_text.startswith("!") else "ignored",
        })
    by_path = {row["path"]: row for row in records}
    ordered = []
    for path in paths:
        row = by_path.get(path)
        ordered.append(dict(row) if row is not None else {
            "path": path,
            "status": "not_measured" if truncated else "not_ignored",
        })
    return {"records": ordered, "truncated": truncated}

# Repository metadata: one checkout identity shared by every repository.
# Git identity lives here; app-specific build data (sizes, screens) does not.
REPOSITORY_METADATA_SCHEMA = "2.0"
REPOSITORY_METADATA_V1_SCHEMA = "1.0"
_RM_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_RM_NAME = re.compile(r"[A-Za-z0-9_.-]+")
_RM_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_RM_TOOL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}")
_RM_TOOL_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+ -]{0,31}")
_RM_MEASUREMENTS = ("github_reported_size_bytes", "working_tree_bytes", "release_artifact_bytes")
_RM_TOOL_ARGS = {"git": "--version", "gh": "--version", "node": "--version", "npm": "--version", "npx": "--version"}
_RM_VERSION = r"[0-9]+(?:\.[0-9]+)+(?:[-+._][0-9A-Za-z][0-9A-Za-z.-]*)?"
_RM_TOOL_PATTERNS = {
    "git": re.compile(r"git version (?P<v>" + _RM_VERSION + r")(?:\s.*)?"),
    "gh": re.compile(r"gh version (?P<v>" + _RM_VERSION + r")(?:\s.*)?"),
    "node": re.compile(r"v(?P<v>" + _RM_VERSION + r")"),
    "npm": re.compile(r"(?P<v>" + _RM_VERSION + r")"),
    "npx": re.compile(r"(?P<v>" + _RM_VERSION + r")"),
    # Same acceptance as Ironmate's _PACKAGE_VERSION (epoch, pre, post, dev, local).
    "python": re.compile(r"(?P<v>(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?"
                         r"(?:\.dev[0-9]+)?(?:\+(?:[0-9A-Za-z][0-9A-Za-z.-]*)?)?)"),
}


def _rm_full_name(full_name):
    if not isinstance(full_name, str) or full_name.count("/") != 1:
        raise ValueError("repository must be owner/name")
    owner, name = full_name.split("/")
    if not _RM_OWNER.fullmatch(owner) or not _RM_NAME.fullmatch(name) or name in (".", ".."):
        raise ValueError("repository must be owner/name")
    return full_name


def _rm_sha(value, name="sha"):
    if not isinstance(value, str) or not _RM_SHA.fullmatch(value.lower()):
        raise ValueError(name + " must be a full 40- or 64-hex commit SHA")
    return value.lower()


def _rm_time(value, name):
    if not isinstance(value, str):
        raise ValueError(name + " must be an ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(name + " must be an ISO 8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(name + " must include a timezone")
    return value


def _rm_git(root, *args):
    raw, truncated, _ = _git_run(root, ["-c", "i18n.logOutputEncoding=UTF-8", *args], max_bytes=64 * 1024)
    if truncated:
        raise GitInspectionError("git output exceeded the metadata bound")
    return _git_decode(raw).strip()


def checkout_head(root="."):
    """The commit actually checked out at root (never GITHUB_SHA or other env)."""
    sha = _rm_sha(_rm_git(root, "rev-parse", "--verify", "HEAD^{commit}"), "HEAD")
    fields = _rm_git(root, "show", "-s", "--format=%cI%x1f%s", "HEAD").split("\x1f", 1)
    if len(fields) != 2 or not fields[1]:
        raise GitInspectionError("HEAD has no committer date or subject")
    branch = _rm_git(root, "branch", "--show-current") or None
    return {"sha": sha, "branch": branch, "detached": branch is None,
            "timestamp": _rm_time(fields[0], "timestamp"), "subject": fields[1].splitlines()[0]}


_RM_REMOTE = (re.compile(r"(?:https?|ssh|git)://(?:[^@/]+@)?github\.com(?::\d+)?/([^/]+)/([^/]+?)(?:\.git)?/?"),
              re.compile(r"[^@\s/]+@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?"))


def remote_repository(root=".", remote="origin"):
    """owner/name of a GitHub remote, or None; the URL itself is never returned.

    Credentials, proxies and non-GitHub hosts are not echoed: an unrecognised
    remote yields {"full_name": None, "reason": "not_github"}.
    """
    if not isinstance(remote, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", remote):
        raise ValueError("remote must be a plain remote name")
    raw, _, code = _git_run(root, ["remote", "get-url", remote], max_bytes=4096, ok=(0, 2, 128))
    if code:
        return {"remote": remote, "full_name": None, "reason": "no_remote"}
    url = _git_decode(raw).strip()
    for pattern in _RM_REMOTE:
        match = pattern.fullmatch(url)
        if match:
            try:
                return {"remote": remote, "full_name": _rm_full_name(match.group(1) + "/" + match.group(2)), "reason": None}
            except ValueError:
                break
    return {"remote": remote, "full_name": None, "reason": "not_github"}


def _git_subtree_available(root):
    """True when the contrib `git subtree` command is installed (exec-path or PATH)."""
    raw, _, _ = _git_run(root, ["--exec-path"], max_bytes=64 * 1024)
    exec_path = _git_decode(raw).strip()
    if exec_path and os.path.isfile(os.path.join(exec_path, "git-subtree")):
        return True
    return shutil.which("git-subtree") is not None


def _subtree_prefix(prefix):
    if not isinstance(prefix, str) or not prefix.strip():
        raise ValueError("prefix required")
    if any(c in prefix for c in ("\0", "\n", "\r")) or prefix.startswith("-"):
        raise ValueError("invalid prefix")
    if os.path.isabs(prefix) or prefix.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", prefix):
        raise ValueError("prefix must be relative to the repository top level")
    parts = [part for part in prefix.replace("\\", "/").split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts) or parts[0] == ".git":
        raise ValueError("prefix must stay inside the repository")
    if any(part.startswith("-") for part in parts):
        raise ValueError("invalid prefix")
    return "/".join(parts)


def git_subtree_split(root=".", prefix=None, revision="HEAD", *, timeout=600):
    """Read-only `git subtree split --prefix=<dir> <sha>`: return the split commit SHA.

    Never passes --branch/--rejoin/--onto/--annotate, so no ref is created or
    moved and no commit is added to the current branch. Requires the contrib
    `git subtree` command; when it is missing, reason is subtree_unavailable.
    """
    prefix = _subtree_prefix(prefix)
    if not isinstance(revision, str) or not revision or revision.startswith("-") or any(c in revision for c in ("\0", "\n", "\r")):
        raise ValueError("invalid revision")
    base = {"schema": "gh-identity-subtree-split/1", "prefix": prefix, "write": False}
    raw, _, code = _git_run(root, ["rev-parse", "--verify", "-q", "--end-of-options", revision + "^{commit}"],
                            max_bytes=4096, ok=(0, 1, 128))
    if code != 0:
        raise ValueError("revision not found")
    source = _git_decode(raw).strip()
    raw, _, code = _git_run(root, ["cat-file", "-t", source + ":" + prefix], max_bytes=4096, ok=(0, 1, 128))
    if code != 0 or _git_decode(raw).strip() != "tree":
        return {**base, "source": source, "sha": None, "reason": "prefix_not_found"}
    if not _git_subtree_available(root):
        return {**base, "source": source, "sha": None, "reason": "subtree_unavailable",
                "detail": "git subtree (contrib) is not installed"}
    # git-subtree leaves $GIT_DIR/subtree-cache/<pid> behind; remove only what
    # this call created so the checkout's .git is left as it was.
    git_dir = Path(_git_decode(_git_run(root, ["rev-parse", "--absolute-git-dir"], max_bytes=64 * 1024)[0]).strip())
    cache = git_dir / "subtree-cache"
    cache_existed = cache.is_dir()
    before = set(os.listdir(cache)) if cache_existed else set()
    try:
        raw, _, _ = _git_run(root, ["subtree", "split", "-q", "--prefix=" + prefix, source],
                             max_bytes=64 * 1024, timeout=timeout)
    finally:
        if cache.is_dir() and not cache.is_symlink():
            for name in set(os.listdir(cache)) - before:
                shutil.rmtree(cache / name, ignore_errors=True)
            if not cache_existed:
                with contextlib.suppress(OSError):
                    cache.rmdir()
    sha = _git_decode(raw).strip().splitlines()[-1:] or [""]
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha[0]):
        raise GitInspectionError("unexpected git subtree output")
    return {**base, "source": source, "sha": sha[0], "reason": None}


_SUBTREE_TRAILER = "Subtree-Split"


def _rev_commit(root, revision):
    if not isinstance(revision, str) or not revision or revision.startswith("-") or any(c in revision for c in ("\0", "\n", "\r")):
        raise ValueError("invalid revision")
    raw, _, code = _git_run(root, ["rev-parse", "--verify", "-q", "--end-of-options", revision + "^{commit}"],
                            max_bytes=4096, ok=(0, 1, 128))
    if code != 0:
        raise ValueError("revision not found")
    return _git_decode(raw).strip()


def _rev_list(root, sha, max_bytes=16 * 1024 * 1024):
    raw, truncated, _ = _git_run(root, ["rev-list", "--end-of-options", sha], max_bytes=max_bytes)
    if truncated:
        raise GitInspectionError("rev-list exceeds output limit")
    return _git_decode(raw).split()


def _changed_paths(root, sha):
    raw, truncated, _ = _git_run(root, ["diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root", "-m", sha],
                                 max_bytes=4 * 1024 * 1024)
    if truncated:
        raise GitInspectionError("diff-tree exceeds output limit")
    return sorted({p for p in _git_decode(raw).split("\0") if p})


def subtree_sync_status(root=".", prefix=None, other_root=None, *, revision="HEAD", other_ref="HEAD",
                        other_only_paths=(), max_listed=50, timeout=600, mainline=None):
    """Compare `git subtree split --prefix` of a monorepo with a split-out checkout.

    Local only, read-only, no fetch. git subtree split is deterministic, so the
    split commits of the monorepo carry the same SHAs as the history of the
    split-out repository; both histories are compared as SHA sets from their
    own object databases. Commits of the other side touching only
    `other_only_paths` (fnmatch patterns, e.g. repo-only files) are not counted.
    """
    if other_root is None:
        raise ValueError("other_root required")
    patterns = tuple(other_only_paths or ())
    if any(not isinstance(p, str) or not p for p in patterns):
        raise ValueError("invalid other_only_paths")
    # Validate every input before the git-subtree availability check so bad
    # arguments are rejected the same way whether or not git subtree exists.
    _subtree_prefix(prefix)
    _rev_commit(root, revision)
    other_head = _rev_commit(other_root, other_ref)
    split = git_subtree_split(root, prefix, revision, timeout=timeout)
    base = {"schema": "gh-identity-subtree-sync/1", "prefix": split["prefix"], "write": False,
            "source": split["source"], "split_sha": split["sha"]}
    if split["reason"]:
        return {**base, "state": None, "reason": split["reason"], "other_head": other_head}
    mono = _rev_list(root, split["sha"])
    other = _rev_list(other_root, other_head)
    mono_set, other_set = set(mono), set(other)
    mono_extra = [c for c in mono if c not in other_set]
    other_extra, ignored = [], []
    for c in other:
        if c in mono_set:
            continue
        paths = _changed_paths(other_root, c)
        if patterns and paths and all(any(fnmatch.fnmatchcase(p, pat) for pat in patterns) for p in paths):
            ignored.append(c)
        else:
            other_extra.append(c)
    if not mono_set & other_set:
        state = "unrelated"
    elif mono_extra and other_extra:
        state = "diverged"
    elif mono_extra:
        state = "monorepo_ahead"
    elif other_extra:
        state = "other_ahead"
    else:
        state = "in_sync"
    on_mainline = None
    if mainline is not None:
        main_sha = _rev_commit(root, mainline)
        raw, truncated, _ = _git_run(root, ["rev-list", "--first-parent", "--end-of-options", main_sha], max_bytes=16 * 1024 * 1024)
        if truncated:
            raise GitInspectionError("rev-list exceeds output limit")
        on_mainline = split["source"] in _git_decode(raw).split()
    return {**base, "other_head": other_head, "split_in_other": split["sha"] in other_set,
            "source_on_first_parent": on_mainline,
            "other_head_in_split": other_head in mono_set,
            "monorepo_ahead": len(mono_extra), "other_ahead": len(other_extra), "other_only_ignored": len(ignored),
            "monorepo_ahead_commits": mono_extra[:max_listed], "other_ahead_commits": other_extra[:max_listed],
            "state": state, "reason": None}


def _parse_split_trailer(message):
    found = []
    for line in message.splitlines():
        m = re.fullmatch(_SUBTREE_TRAILER + r":\s*([0-9a-f]{40}|[0-9a-f]{64})\s+([0-9a-f]{40}|[0-9a-f]{64})\s+(\S+)\s*", line.strip())
        if m:
            found.append({"source": m.group(1), "split_sha": m.group(2), "prefix": m.group(3)})
    return found


def sync_provenance(root=".", prefix=None, other_root=None, *, revision="HEAD", other_ref="HEAD",
                    direction=None, squash=False, generated_at=None, other_only_paths=(), timeout=600):
    """Provenance record of a subtree sync: which monorepo SHA / split SHA it came from.

    Read-only. The suggested commit trailer is
    `Subtree-Split: <monorepo_sha> <split_sha> <prefix>`.
    """
    if direction not in (None, "push", "pull"):
        raise ValueError("direction must be push or pull")
    if generated_at is not None and not isinstance(generated_at, str):
        raise ValueError("generated_at must be a string")
    status = subtree_sync_status(root, prefix, other_root, revision=revision, other_ref=other_ref,
                                 other_only_paths=other_only_paths, timeout=timeout)
    def ident(path, sha):
        return {"repository": remote_repository(path).get("full_name"), "sha": sha}
    record = {"schema": "gh-identity-sync-provenance/1", "write": False, "prefix": status["prefix"],
              "monorepo": ident(root, status["source"]), "split_sha": status["split_sha"],
              "other": ident(other_root, status.get("other_head")), "state": status["state"],
              "direction": direction, "squash": bool(squash),
              "generated_at": generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "reason": status["reason"]}
    if status["split_sha"]:
        record["trailer"] = f"{_SUBTREE_TRAILER}: {status['source']} {status['split_sha']} {status['prefix']}"
    return record


def verify_sync_provenance(root=".", commit="HEAD", *, monorepo_root=None, timeout=600):
    """Check Subtree-Split trailers of `commit` (in root). With monorepo_root, the
    split is recomputed from the recorded monorepo SHA and must match."""
    sha = _rev_commit(root, commit)
    raw, _, _ = _git_run(root, ["log", "-1", "--format=%B", sha], max_bytes=256 * 1024)
    trailers = _parse_split_trailer(_git_decode(raw))
    base = {"schema": "gh-identity-sync-provenance-check/1", "write": False, "commit": sha}
    if not trailers:
        return {**base, "trailers": [], "ok": False, "reason": "no_trailer"}
    results = []
    for t in trailers:
        row = dict(t)
        if monorepo_root is not None:
            try:
                again = git_subtree_split(monorepo_root, t["prefix"], t["source"], timeout=timeout)
                row["recomputed"] = again["sha"]
                row["ok"] = again["sha"] == t["split_sha"]
                row["reason"] = again["reason"] or (None if row["ok"] else "split_mismatch")
            except ValueError as error:
                row.update(recomputed=None, ok=False, reason=str(error).replace(" ", "_"))
        results.append(row)
    ok = all(r.get("ok", True) for r in results)
    return {**base, "trailers": results, "ok": ok, "reason": None if ok else "mismatch"}


def write_sync_provenance(record, output_dir):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "sync-provenance.json").write_text(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"json": "sync-provenance.json"}


def _python_imports(source):
    """({absolute top-level module: first line}, has_relative_import) from Python source; never executed."""
    import ast
    tree = ast.parse(source)
    names, relative = {}, False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.setdefault(a.name.split(".")[0], node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                relative = True
            elif node.module:
                names.setdefault(node.module.split(".")[0], node.lineno)
    return names, relative


def _stdlib_names():
    return set(getattr(sys, "stdlib_module_names", ())) | set(sys.builtin_module_names) | {"__future__"}


def _git_blobs(root, specs, max_bytes=64 * 1024 * 1024):
    """Read `<rev>:<path>` blobs with one `git cat-file --batch`; returns {spec: bytes}."""
    if not specs:
        return {}
    raw, truncated, _ = _git_run(root, ["cat-file", "--batch"], input_bytes=("\n".join(specs) + "\n").encode(),
                                 max_bytes=max_bytes)
    if truncated:
        raise GitInspectionError("cat-file exceeds output limit")
    out, pos = {}, 0
    for spec in specs:
        end = raw.index(b"\n", pos)
        header = raw[pos:end].split()
        pos = end + 1
        if len(header) == 3 and header[1] == b"blob":
            size = int(header[2])
            out[spec] = raw[pos:pos + size]
            pos += size + 1
    return out


def split_preflight(root=".", prefix=None, revision="HEAD", *, timeout=600, max_listed=200):
    """Read-only checklist before splitting `prefix` out of a monorepo.

    Lists references into the prefix from outside (imports, paths, sys.path,
    config), references from the prefix to the rest of the repository,
    third-party imports inside the prefix, and the split SHA / history size.
    """
    prefix = _subtree_prefix(prefix)
    source = _rev_commit(root, revision)
    raw, truncated, _ = _git_run(root, ["ls-tree", "-r", "-z", "--name-only", source], max_bytes=32 * 1024 * 1024)
    if truncated:
        raise GitInspectionError("ls-tree exceeds output limit")
    files = [p for p in _git_decode(raw).split("\0") if p]
    inside = [p for p in files if p.startswith(prefix + "/")]
    outside = [p for p in files if not p.startswith(prefix + "/")]
    base = {"schema": "gh-identity-split-preflight/1", "write": False, "prefix": prefix, "source": source}
    if not inside:
        return {**base, "reason": "prefix_not_found", "checks": []}
    tops = sorted({p.split("/", 1)[0] for p in outside if "/" in p} - {".git", ".github"})
    def modules(paths, strip):
        mods = set()
        for p in paths:
            rest = p[len(strip):] if strip else p
            parts = rest.split("/")
            if parts[-1].endswith(".py"):
                mods.add(parts[-1][:-3])
            if len(parts) > 1:
                mods.add(parts[0])
        mods.discard("__init__")
        return mods
    prefix_mods = modules(inside, prefix + "/")
    outside_mods = modules([p for p in outside if p.endswith(".py")], "") - prefix_mods
    blobs = _git_blobs(root, [source + ":" + p for p in files if p.endswith(".py")])
    inbound, outbound, third, syntax = [], [], set(), []
    stdlib = _stdlib_names()
    for p in files:
        data = blobs.get(source + ":" + p)
        if data is None:
            continue
        try:
            names, _ = _python_imports(data.decode("utf-8", "replace"))
        except SyntaxError:
            syntax.append(p)
            continue
        if p in inside:
            for n in sorted(names):
                if n in prefix_mods or n in stdlib:
                    continue
                if n in outside_mods:
                    outbound.append({"file": p, "line": names[n], "kind": "import", "target": n})
                else:
                    third.add(n)
        else:
            for n in sorted(set(names) & prefix_mods):
                inbound.append({"file": p, "line": names[n], "kind": "import", "target": n})
    def grep(needles, pathspec):
        args = ["grep", "-n", "-I", "-F"]
        for needle in needles:
            args += ["-e", needle]
        raw, _, code = _git_run(root, [*args, source, "--", *pathspec], max_bytes=4 * 1024 * 1024, ok=(0, 1))
        rows = []
        for line in _git_decode(raw).splitlines():
            parts = line.split(":", 3)
            if len(parts) == 4:
                rows.append((parts[1], int(parts[2]), parts[3]))
        return rows
    config_ext = (".toml", ".yml", ".yaml", ".cfg", ".ini", ".json")
    for path, line, text in grep([prefix + "/", '"' + prefix + '"', "'" + prefix + "'"], [":(exclude)" + prefix]):
        kind = "sys.path" if "sys.path" in text else "config" if path.endswith(config_ext) else "path"
        inbound.append({"file": path, "line": line, "kind": kind, "target": text.strip()[:200]})
    if tops:
        needles = [t + "/" for t in tops] + ["'" + t + "'" for t in tops] + ['"' + t + '"' for t in tops]
        for path, line, text in grep(needles, [prefix]):
            outbound.append({"file": path, "line": line, "kind": "sys.path" if "sys.path" in text else "path",
                             "target": text.strip()[:200]})
    split = git_subtree_split(root, prefix, source, timeout=timeout)
    history = len(_rev_list(root, split["sha"])) if split["sha"] else None
    config_refs = sorted({f"{r['file']}:{r['line']}" for r in inbound if r["kind"] == "config"})
    checks = [
        {"id": "split", "ok": split["sha"] is not None, "detail": split["reason"]},
        {"id": "inbound_refs", "ok": not inbound, "detail": len(inbound)},
        {"id": "outbound_refs", "ok": not outbound, "detail": len(outbound)},
        {"id": "third_party_imports", "ok": not third, "detail": sorted(third)},
        {"id": "syntax", "ok": not syntax, "detail": syntax[:max_listed]},
    ]
    return {**base, "split_sha": split["sha"], "history_commits": history,
            "files": len(inside), "inbound_refs": inbound[:max_listed], "outbound_refs": outbound[:max_listed],
            "inbound_total": len(inbound), "outbound_total": len(outbound),
            "third_party_imports": sorted(third), "config_refs": config_refs,
            "checks": checks, "ok": all(c["ok"] for c in checks), "reason": split["reason"]}


VENDOR_LOCK_SCHEMA = "vendor-lock/1"
VENDOR_RULES_SCHEMA = "vendor-rules/1"
_NOT_FOUND_CODES = ("not_found_or_inaccessible", "gh_not_found", "not_found", "http_404")
_VENDOR_FIELDS = {"repository", "ref", "commit", "source", "destination", "blob_sha", "sha256"}


def git_blob_sha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _vendor_path(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("expected a relative POSIX path")
    parts = value.split("/")
    if any(p in ("", ".", "..", ".git") for p in parts) or any(ord(c) < 32 for c in value):
        raise ValueError("unsafe path: " + value)
    return value


def validate_vendor_lock(lock):
    """vendor-lock/1 validation, compatible with Ironmate vendor/vendor_sync.py."""
    if not isinstance(lock, dict) or set(lock) != {"schema", "files"} or lock["schema"] != VENDOR_LOCK_SCHEMA:
        raise ValueError("expected vendor-lock/1")
    if not isinstance(lock["files"], list) or not lock["files"]:
        raise ValueError("files must be a nonempty list")
    seen = set()
    for item in lock["files"]:
        if not isinstance(item, dict) or set(item) != _VENDOR_FIELDS:
            raise ValueError("invalid file fields")
        if not isinstance(item["repository"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", item["repository"]) \
                or any(p in (".", "..") for p in item["repository"].split("/")):
            raise ValueError("expected owner/repository")
        _vendor_path(item["ref"]); _vendor_path(item["source"])
        dest = _vendor_path(item["destination"]).casefold()
        if dest in seen:
            raise ValueError("duplicate destination")
        seen.add(dest)
        for key, n in (("commit", 40), ("blob_sha", 40), ("sha256", 64)):
            if not isinstance(item[key], str) or not re.fullmatch(f"[0-9a-f]{{{n}}}", item[key]):
                raise ValueError("invalid " + key)
    for d in seen:
        if any(d.startswith(o + "/") for o in seen if o != d):
            raise ValueError("overlapping destinations")
    return lock


def validate_vendor_rules(rules):
    """vendor-rules/1: placement rules for vendored single files (see README)."""
    if not isinstance(rules, dict) or rules.get("schema") != VENDOR_RULES_SCHEMA:
        raise ValueError("expected vendor-rules/1")
    allowed = {"schema", "root", "require", "layout", "deny_sources", "overrides"}
    if set(rules) - allowed:
        raise ValueError("unknown rules fields: " + ",".join(sorted(set(rules) - allowed)))
    out = {"root": _vendor_path(rules.get("root", "vendor")),
           "require": {"stdlib_only": True, "single_file": True, "imported_by_project": True,
                       "pinned_commit": True, "license_sidecar": True, "unmodified_bytes": True},
           "layout": {"module": "{root}/{basename}", "license": "{root}/{repo}-LICENSE"},
           "deny_sources": [], "overrides": []}
    require = rules.get("require", {})
    if not isinstance(require, dict) or set(require) - set(out["require"]) or any(type(v) is not bool for v in require.values()):
        raise ValueError("invalid require")
    out["require"].update(require)
    layout = rules.get("layout", {})
    if not isinstance(layout, dict) or any(not isinstance(v, str) for v in layout.values()):
        raise ValueError("invalid layout")
    out["layout"].update(layout)
    deny = rules.get("deny_sources", [])
    if not isinstance(deny, list) or any(not isinstance(p, str) or not p for p in deny):
        raise ValueError("invalid deny_sources")
    out["deny_sources"] = deny
    for o in rules.get("overrides", []):
        if not isinstance(o, dict) or set(o) != {"repository", "source", "destination", "reason"} \
                or not isinstance(o["reason"], str) or not o["reason"].strip():
            raise ValueError("override needs repository, source, destination and a reason")
        _vendor_path(o["source"]); _vendor_path(o["destination"])
        out["overrides"].append(o)
    return out


def _vendor_destination(rules, repository, source, kind="module"):
    for o in rules["overrides"]:
        if kind == "module" and (o["repository"], o["source"]) == (repository, source):
            return o["destination"], "override"
    template = rules["layout"].get(kind)
    if template is None:
        return None, None
    name = repository.split("/", 1)[1]
    dest = template.format(root=rules["root"], basename=source.rsplit("/", 1)[-1], repo=name, source=source)
    return _vendor_path(dest), "layout"


def _load_json_file(path, what):
    try:
        with open(path, "rb") as stream:
            return json.loads(stream.read(8 * 1024 * 1024 + 1))
    except FileNotFoundError:
        raise ValueError(what + " not found") from None
    except json.JSONDecodeError:
        raise ValueError(what + " is not JSON") from None


def _safe_file(root, relative):
    root = Path(root).resolve()
    path = root
    for part in PurePosixPath(_vendor_path(relative)).parts:
        path = path / part
        if path.is_symlink():
            return None, "symlink"
    if not path.resolve().is_relative_to(root):
        return None, "outside_root"
    return path, None


def _strip_header(data, max_lines=5):
    """Candidates with up to max_lines leading '#' comment lines removed (shebang kept)."""
    lines = data.splitlines(keepends=True)
    keep = 1 if lines and lines[0].startswith(b"#!") else 0
    out = []
    for n in range(1, max_lines + 1):
        body = lines[keep:keep + n]
        if len(body) < n or not all(l.lstrip().startswith(b"#") for l in body):
            break
        out.append(b"".join(lines[:keep] + lines[keep + n:]))
    return out


def vendor_lock_check(root=".", lock="vendor.lock.json", rules=None, parent_ref=None):
    """Compare vendor.lock.json (vendor-lock/1) with the files on disk. Local, read-only."""
    root_path = Path(root).resolve()
    lock_path = root_path / lock if not os.path.isabs(lock) else Path(lock)
    data = validate_vendor_lock(_load_json_file(lock_path, "lock"))
    parsed = validate_vendor_rules(_load_json_file(Path(root) / rules if not os.path.isabs(rules) else rules, "rules")) \
        if isinstance(rules, str) else (validate_vendor_rules(rules) if rules is not None else None)
    rows, violations = [], []
    for item in data["files"]:
        row = {"destination": item["destination"], "repository": item["repository"], "source": item["source"],
               "commit": item["commit"], "rule": None}
        path, problem = _safe_file(root_path, item["destination"])
        if problem:
            row["status"] = problem
        elif not path.is_file():
            row["status"] = "missing"
        else:
            body = path.read_bytes()
            if git_blob_sha(body) == item["blob_sha"] and hashlib.sha256(body).hexdigest() == item["sha256"]:
                row["status"] = "ok"
            elif any(git_blob_sha(c) == item["blob_sha"] for c in _strip_header(body)):
                row["status"] = "header_modified"
            else:
                row["status"] = "digest_mismatch"
        if parsed is not None:
            kind = "license" if item["source"].rsplit("/", 1)[-1].upper().startswith(("LICENSE", "COPYING")) else "module"
            expected, rule = _vendor_destination(parsed, item["repository"], item["source"], kind)
            row["rule"] = rule
            if expected is not None and expected != item["destination"]:
                violations.append({"destination": item["destination"], "rule": "layout", "expected": expected})
            if parsed["require"]["pinned_commit"] and item["ref"] != item["commit"]:
                violations.append({"destination": item["destination"], "rule": "pinned_commit", "expected": item["commit"]})
        rows.append(row)
    locked = {r["destination"] for r in rows}
    roots = {d.rsplit("/", 1)[0] for d in locked if "/" in d}
    if parsed is not None:
        roots.add(parsed["root"])
    for r in sorted(roots):
        base, problem = _safe_file(root_path, r)
        if problem or not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(root_path).as_posix()
            if p.is_file() and rel not in locked and "__pycache__" not in p.parts and p.suffix != ".pyc" \
                    and p.name not in ("README.md", ".gitkeep") and not any(rel.startswith(o + "/") for o in locked):
                rows.append({"destination": rel, "status": "unlocked", "repository": None, "source": None,
                             "commit": None, "rule": None})
    pref = parent_ref_status(root_path, parent_ref)
    ok = all(r["status"] == "ok" for r in rows) and not violations and pref["status"] != "invalid" \
        and not (parent_ref and pref["status"] == "missing")
    return {"schema": "gh-identity-vendor-check/1", "write": False, "parent_ref": pref, "lock": lock_path.relative_to(root_path).as_posix()
            if lock_path.is_relative_to(root_path) else str(lock_path), "files": rows, "violations": violations,
            "counts": dict(Counter(r["status"] for r in rows)), "ok": ok}


def _remote_file(repository, path, commit, transport="auto", timeout=30, max_bytes=8 * 1024 * 1024):
    """Bytes of repository@commit:path via the contents and git/blobs API (read-only)."""
    r = repo(repository)
    meta = request("GET", f"repos/{r}/contents/{urllib.parse.quote(_vendor_path(path))}?ref={commit}",
                   transport=transport, timeout=timeout)
    if not isinstance(meta, dict) or meta.get("type") != "file" or not isinstance(meta.get("sha"), str):
        raise Error("not_a_file")
    if not isinstance(meta.get("size"), int) or meta["size"] > max_bytes:
        raise Error("file_too_large")
    blob = request("GET", f"repos/{r}/git/blobs/{meta['sha']}", transport=transport, timeout=timeout)
    if not isinstance(blob, dict) or blob.get("encoding") != "base64":
        raise Error("invalid_blob")
    data = base64.b64decode(blob.get("content", ""))
    if git_blob_sha(data) != meta["sha"]:
        raise Error("blob_identity_mismatch")
    return data


def _remote_exists(repository, path, commit, transport="auto", timeout=30):
    try:
        _remote_file(repository, path, commit, transport, timeout)
        return True
    except Error as e:
        if e.code in _NOT_FOUND_CODES or e.code == "not_a_file":
            return False
        raise


def vendor_lock_verify_remote(root=".", lock="vendor.lock.json", *, upstream=False, first_parent=False,
                              check_parent_ref=False, parent_ref=None, transport="auto", timeout=30, max_pages=10):
    """Check each lock entry against upstream repository@commit:source (read-only network)."""
    root_path = Path(root).resolve()
    data = validate_vendor_lock(_load_json_file(root_path / lock if not os.path.isabs(lock) else lock, "lock"))
    rows, fp_cache = [], {}
    for item in data["files"]:
        row = {"destination": item["destination"], "repository": item["repository"], "source": item["source"],
               "commit": item["commit"]}
        try:
            body = _remote_file(item["repository"], item["source"], item["commit"], transport, timeout)
            blob_ok = git_blob_sha(body) == item["blob_sha"]
            sha_ok = hashlib.sha256(body).hexdigest() == item["sha256"]
            row["status"] = "ok" if blob_ok and sha_ok else "digest_mismatch"
        except Error as e:
            row["status"] = "unavailable"
            row["detail"] = e.code
        if upstream:
            try:
                hist = request("GET", f"repos/{repo(item['repository'])}/commits?path={urllib.parse.quote(item['source'])}&per_page=1",
                               transport=transport, timeout=timeout)
                head = hist[0]["sha"] if isinstance(hist, list) and hist and isinstance(hist[0], dict) else None
            except Error:
                head = None
            row["upstream_head"] = head
            row["upstream_changed"] = None if head is None else head != item["commit"]
        if first_parent:
            key = (item["repository"], item["commit"])
            if key not in fp_cache:
                try:
                    fp_cache[key] = _first_parent_contains(item["repository"], item["commit"], transport=transport,
                                                           timeout=timeout, max_pages=max_pages)
                except Error:
                    fp_cache[key] = None
            row["first_parent"] = fp_cache[key]
        rows.append(row)
    out = {"schema": "gh-identity-vendor-verify-remote/1", "write": False, "files": rows,
           "counts": dict(Counter(r["status"] for r in rows)),
           "ok": all(r["status"] == "ok" and r.get("first_parent") is not False for r in rows)}
    if check_parent_ref:
        pref = parent_ref_status(root_path, parent_ref)
        if pref["status"] == "ok":
            pref["remote"] = verify_parent_ref({"schema": PARENT_REF_SCHEMA, **pref["ref"]}, transport=transport, timeout=timeout)
        out["parent_ref"] = pref
        out["ok"] = out["ok"] and pref["status"] == "ok" and pref["remote"]["status"] == "ok"
    return out


def _parse_candidate(text):
    m = re.fullmatch(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+):([^@]+)@([^@]+)", text or "")
    if not m:
        raise ValueError("candidate must be OWNER/REPO:PATH@SHA")
    return m.group(1), _vendor_path(m.group(2)), m.group(3)


def _project_imports(root, exclude):
    names = set()
    root = Path(root).resolve()
    for p in root.rglob("*.py"):
        rel = p.relative_to(root).as_posix()
        if any(rel == e or rel.startswith(e + "/") for e in exclude) or ".git" in p.parts or "__pycache__" in p.parts:
            continue
        try:
            names.update(_python_imports(p.read_text(encoding="utf-8", errors="replace"))[0])
        except SyntaxError:
            continue
    return names


def vendor_plan(root=".", candidates=(), rules=None, *, transport="auto", timeout=30):
    """Rule-based placement plan for vendoring single files (read-only; writes nothing).

    Rules are applied in a fixed order: pinned commit, upstream existence,
    deny list, standard library only, single file, imported by the project,
    then destination from layout (or an override with a reason).
    """
    parsed = validate_vendor_rules(rules if isinstance(rules, dict) else
                                   _load_json_file(Path(root) / rules if rules and not os.path.isabs(rules) else rules, "rules")
                                   if rules else {"schema": VENDOR_RULES_SCHEMA})
    req = parsed["require"]
    stdlib = _stdlib_names()
    used = _project_imports(root, [parsed["root"], ".test-deps", "build"]) if req["imported_by_project"] else set()
    decisions = []
    for text in candidates:
        repository, source, ref = _parse_candidate(text)
        reasons, d = [], {"candidate": text, "repository": repository, "source": source, "commit": ref}
        def skip(reason):
            reasons.append(reason)
        if req["pinned_commit"] and not re.fullmatch(r"[0-9a-f]{40}", ref):
            skip("not_pinned_to_commit")
        basename = source.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatchcase(basename, p) or fnmatch.fnmatchcase(source, p) for p in parsed["deny_sources"]):
            skip("denied_source")
        body = None
        if not reasons:
            try:
                body = _remote_file(repository, source, ref, transport, timeout)
            except Error as e:
                skip("upstream_unavailable:" + e.code)
        if body is not None:
            d["blob_sha"], d["sha256"] = git_blob_sha(body), hashlib.sha256(body).hexdigest()
            if source.endswith(".py"):
                try:
                    imports, relative = _python_imports(body.decode("utf-8", "replace"))
                except SyntaxError:
                    imports, relative = {}, False
                    skip("syntax_error")
                third = sorted(n for n in imports if n not in stdlib and n != basename[:-3])
                d["third_party_imports"] = third
                if req["stdlib_only"] and third:
                    skip("not_stdlib_only")
                if req["single_file"] and relative:
                    skip("relative_import")
                if req["imported_by_project"] and basename[:-3] not in used:
                    skip("not_imported_by_project")
        dest, rule = _vendor_destination(parsed, repository, source)
        d["destination"], d["rule"] = dest, rule
        if body is not None and not reasons and req["license_sidecar"]:
            if _remote_exists(repository, "LICENSE", ref, transport, timeout):
                d["license_destination"] = _vendor_destination(parsed, repository, "LICENSE", "license")[0]
            else:
                skip("license_missing")
        d["action"] = "skip" if reasons else "place"
        d["reasons"] = reasons
        decisions.append(d)
    return {"schema": "gh-identity-vendor-plan/1", "write": False, "root": parsed["root"], "decisions": decisions,
            "counts": dict(Counter(x["action"] for x in decisions))}


def vendor_matrix(owner, repos=None, *, lock_paths=("vendor.lock.json", "tool/python/vendor.lock.json"),
                  heads=None, include_forks=False, transport="auto", timeout=30, max_repos=100):
    """Who vendors whom at which commit, across repositories (read-only network).

    Reads each repository's vendor.lock.json from its default branch and flags
    pin_mismatch (one upstream source pinned at different commits) and the
    placement roots each consumer uses.
    """
    if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", owner):
        raise ValueError("invalid owner")
    targets, redacted = _public_targets(owner, repos, include_forks=include_forks, transport=transport, timeout=timeout)
    names = [t["name"] for t in targets][:max_repos]
    edges, consumers, errors = [], {}, []
    for name in names:
        full = repo(f"{owner}/{name}")
        found = None
        for lp in lock_paths:
            try:
                at = (heads or {}).get(full)
                meta = request("GET", f"repos/{full}/contents/{lp}" + (f"?ref={at}" if at else ""), transport=transport, timeout=timeout)
            except Error as e:
                if e.code in _NOT_FOUND_CODES:
                    continue
                errors.append({"repository": full, "detail": e.code}); break
            if isinstance(meta, dict) and meta.get("encoding") == "base64":
                try:
                    found = (lp, validate_vendor_lock(json.loads(base64.b64decode(meta.get("content", "")))))
                except (ValueError, json.JSONDecodeError) as error:
                    errors.append({"repository": full, "detail": "invalid_lock: " + str(error)})
                break
        if not found:
            continue
        lp, lock = found
        roots = sorted({f["destination"].rsplit("/", 1)[0] for f in lock["files"] if "/" in f["destination"]})
        consumers[full] = {"lock": lp, "files": len(lock["files"]), "roots": roots}
        for f in lock["files"]:
            edges.append({"consumer": full, "provider": f["repository"], "source": f["source"],
                          "commit": f["commit"], "destination": f["destination"]})
    pins = {}
    for e in edges:
        pins.setdefault((e["provider"], e["source"]), set()).add(e["commit"])
    mismatches = [{"provider": p, "source": s, "commits": sorted(c),
                   "consumers": sorted({e["consumer"] for e in edges if (e["provider"], e["source"]) == (p, s)})}
                  for (p, s), c in sorted(pins.items()) if len(c) > 1]
    layouts = Counter(r for c in consumers.values() for r in c["roots"])
    return {"schema": "gh-identity-vendor-matrix/1", "write": False, "owner": owner, "consumers": consumers,
            "edges": edges, "pin_mismatch": mismatches, "layouts": dict(layouts), "errors": errors,
            "redacted": redacted}


DUPLICATE_IGNORE = ("README.md", "LICENSE", "LICENSE.md", "LICENSE.txt", "__init__.py", "conftest.py",
                    "pyproject.toml", ".gitignore", ".gitattributes", "requirements.txt", "uv.lock",
                    "package.json", "package-lock.json", "index.html", "index.md", "CHANGELOG.md",
                    "*.png", "*.webp", "*.jpg", "*.svg", "*.ico", "*-LICENSE", ".github/*", "ci.yml",
                    "actionlint.yml", "claude.yml", "pages.yml", "vendor.lock.json")


def owner_repositories(owner, *, include_forks=False, transport="auto", timeout=30):
    """Repositories listed by the API for user `owner` (type=owner, so org repos are not
    listed), filtered explicitly: visibility public, archived false, forks excluded
    unless include_forks. No repository names are hard-coded."""
    if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", owner):
        raise ValueError("invalid owner")
    rows = pages(f"users/{owner}/repos?type=owner&sort=full_name", transport, timeout)
    out = []
    for x in rows:
        if not isinstance(x, dict):
            continue
        visibility = x.get("visibility") or ("private" if x.get("private") else "public")
        if visibility != "public" or x.get("private") or x.get("archived") is not False and x.get("archived") is not None \
                or (x.get("fork") and not include_forks):
            continue
        if (x.get("owner") or {}).get("login", owner).lower() != owner.lower():
            continue
        out.append({"name": x.get("name"), "default_branch": x.get("default_branch")})
    return out


def _repo_tree(full, branch, transport, timeout):
    head = request("GET", f"repos/{full}/commits/{urllib.parse.quote(branch, safe='')}", transport=transport, timeout=timeout)
    if not isinstance(head, dict) or not re.fullmatch(r"[0-9a-f]{40}", str(head.get("sha"))):
        raise Error("invalid_commit")
    d = request("GET", f"repos/{full}/git/trees/{head['sha']}?recursive=1", transport=transport, timeout=timeout)
    if not isinstance(d, dict) or not isinstance(d.get("tree"), list):
        raise Error("invalid_tree_response")
    files = {e["path"]: e["sha"] for e in d["tree"] if isinstance(e, dict) and e.get("type") == "blob"
             and e.get("mode") in ("100644", "100755") and isinstance(e.get("path"), str) and isinstance(e.get("sha"), str)}
    return head["sha"], files, bool(d.get("truncated"))


def _repo_lock(full, ref, paths, lock_paths, transport, timeout):
    for lp in lock_paths:
        if lp not in paths:
            continue
        meta = request("GET", f"repos/{full}/contents/{lp}?ref={ref}", transport=transport, timeout=timeout)
        if isinstance(meta, dict) and meta.get("encoding") == "base64":
            return lp, validate_vendor_lock(json.loads(base64.b64decode(meta.get("content", ""))))
    return None, None


def duplicate_files(owner, repos=None, *, ignore=DUPLICATE_IGNORE, extra_ignore=(), min_repos=2,
                    lock_paths=("vendor.lock.json", "tool/python/vendor.lock.json"), include_forks=False,
                    heads=None, transport="auto", timeout=30):
    """Same basename in several repositories of one owner, with vendor-lock provenance.

    Read-only. Provenance comes only from each repository's vendor-lock/1 entry
    whose destination is the file; otherwise it is null. `identical` compares
    git blob SHAs. A copy is `origin` when some lock in the set points to it,
    and `stale` when its pinned blob differs from the upstream file on the
    upstream default branch (only when the upstream repository was scanned).
    """
    patterns = tuple(ignore) + tuple(extra_ignore)
    errors = []
    targets, redacted = _public_targets(owner, repos, include_forks=include_forks, transport=transport, timeout=timeout)
    scanned, locks, copies = {}, {}, {}
    for t in targets:
        full = repo(f"{owner}/{t['name']}")
        try:
            sha, files, truncated = _repo_tree(full, (heads or {}).get(full) or t["default_branch"] or "HEAD", transport, timeout)
            lp, lock = _repo_lock(full, sha, files, lock_paths, transport, timeout)
        except (Error, ValueError, json.JSONDecodeError) as e:
            errors.append({"repository": full, "detail": getattr(e, "code", str(e))})
            continue
        scanned[full] = {"commit": sha, "files": len(files), "truncated": truncated, "lock": lp,
                         "from_heads": full in (heads or {})}
        locks[full] = {f["destination"]: f for f in (lock or {"files": []})["files"]}
        for path, blob in files.items():
            base = path.rsplit("/", 1)[-1]
            if any(fnmatch.fnmatchcase(base, p) or fnmatch.fnmatchcase(path, p) for p in patterns):
                continue
            copies.setdefault(base, []).append({"repository": full, "path": path, "blob_sha": blob})
    blobs = {}
    for base, rows in copies.items():
        for r in rows:
            blobs[(r["repository"], r["path"])] = r["blob_sha"]
    pointed = {}
    for consumer, entries in locks.items():
        for dest, f in entries.items():
            pointed.setdefault((f["repository"], f["source"]), []).append(consumer)
    groups = []
    for base, rows in sorted(copies.items()):
        repos_in = {r["repository"] for r in rows}
        if len(repos_in) < min_repos:
            continue
        for r in rows:
            f = locks.get(r["repository"], {}).get(r["path"])
            r["provenance"] = None if f is None else {"repository": f["repository"], "source": f["source"], "commit": f["commit"],
                                                     "lock_blob_sha": f["blob_sha"]}
            r["origin"] = (r["repository"], r["path"]) in pointed
            r["consumers"] = sorted(pointed.get((r["repository"], r["path"]), []))
            stale = None
            if f is not None:
                current = blobs.get((f["repository"], f["source"]))
                if current is not None:
                    stale = current != f["blob_sha"]
            r["stale"] = stale
            r["matches_lock"] = None if f is None else r["blob_sha"] == f["blob_sha"]
        distinct = len({r["blob_sha"] for r in rows})
        warnings = []
        if distinct > 1:
            warnings.append("content_differs")
        if any(r["provenance"] is None and not r["origin"] for r in rows):
            warnings.append("unknown_provenance")
        if any(r["stale"] for r in rows):
            warnings.append("stale_pin")
        if any(r["matches_lock"] is False for r in rows):
            warnings.append("modified_after_lock")
        groups.append({"basename": base, "repositories": len(repos_in), "copies": len(rows),
                       "distinct_contents": distinct, "identical": distinct == 1,
                       "warnings": warnings, "files": sorted(rows, key=lambda r: (r["repository"], r["path"]))})
    groups.sort(key=lambda g_: (-g_["repositories"], g_["basename"]))
    all_rows = [r for g_ in groups for r in g_["files"]]
    return {"schema": "gh-identity-duplicate-files/1", "write": False, "owner": owner, "scanned": scanned,
            "errors": errors, "redacted": redacted, "ignore": list(patterns), "groups": groups,
            "counts": {"groups": len(groups), "copies": len(all_rows),
                       "null_provenance": sum(r["provenance"] is None for r in all_rows),
                       "null_provenance_non_origin": sum(r["provenance"] is None and not r["origin"] for r in all_rows),
                       "stale": sum(bool(r["stale"]) for r in all_rows),
                       "groups_with_warnings": sum(bool(g_["warnings"]) for g_ in groups)}}


def render_duplicates_markdown(o, top=30):
    """Markdown summary of duplicate-files for GITHUB_STEP_SUMMARY."""
    c = o["counts"]
    lines = [f"### Duplicate files ({o['owner']}): {len(o['scanned'])} repositories", "",
             f"groups {c['groups']}, copies {c['copies']}, null provenance {c['null_provenance']} "
             f"(non-origin {c['null_provenance_non_origin']}), stale pins {c['stale']}, "
             f"groups with warnings {c['groups_with_warnings']}", ""]
    if o["errors"]:
        lines += ["errors: " + ", ".join(f"{e['repository']} ({e['detail']})" for e in o["errors"]), ""]
    lines += ["| basename | repos | versions | null provenance | stale | origin | warnings |", "|---|---|---|---|---|---|---|"]
    for grp in o["groups"][:max(0, top)]:
        origin = ", ".join(sorted({f["repository"].split("/", 1)[1] for f in grp["files"] if f["origin"]}))
        cells = [grp["basename"], grp["repositories"], grp["distinct_contents"],
                 sum(f["provenance"] is None for f in grp["files"]), sum(bool(f["stale"]) for f in grp["files"]),
                 origin, " ".join(grp["warnings"])]
        lines.append("| " + " | ".join(str(x).replace("|", "\\|") for x in cells) + " |")
    return "\n".join(lines)


def render_vendor_markdown(o):
    """Markdown table for GITHUB_STEP_SUMMARY (vendor-lock-check / verify-remote)."""
    cols = ["destination", "status", "repository", "source", "commit"]
    if any("upstream_changed" in r for r in o["files"]):
        cols.append("upstream_changed")
    lines = [f"### {o['schema']}: {'ok' if o['ok'] else 'NOT ok'}", "", "| " + " | ".join(cols) + " |",
             "|" + "---|" * len(cols)]
    for r in o["files"]:
        cells = []
        for c in cols:
            v = r.get(c)
            v = "" if v is None else (v[:12] if c == "commit" and isinstance(v, str) else str(v))
            cells.append(v.replace("|", "\\|"))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


PARENT_REF_SCHEMA = "parent-pr-ref/1"
PARENT_REF_PATHS = ("templates/parent-pr.ref.json", "parent-pr.ref.json")


def validate_parent_ref(ref):
    """Problems of a parent-pr-ref/1 record (empty list when valid). Local only."""
    problems = []
    if not isinstance(ref, dict):
        return ["not_an_object"]
    if ref.get("schema") != PARENT_REF_SCHEMA:
        problems.append("schema")
    r = ref.get("repository")
    if not isinstance(r, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", r):
        problems.append("repository")
    for key in ("pull_number", "repository_id"):
        if type(ref.get(key)) is not int or ref[key] < 1:
            problems.append(key)
    c = ref.get("commit")
    if not isinstance(c, str) or not re.fullmatch(r"[0-9a-f]{40}", c) or set(c) == {"0"}:
        problems.append("commit")
    if "optional" in ref and not isinstance(ref["optional"], dict):
        problems.append("optional")
    extra = set(ref) - {"schema", "repository", "pull_number", "repository_id", "commit", "optional"}
    if extra:
        problems.append("unknown_fields")
    return problems


def parent_ref_status(root=".", path=None):
    """Find and validate parent-pr.ref.json (templates/ first, then the root)."""
    root_path = Path(root).resolve()
    candidates = (path,) if path else PARENT_REF_PATHS
    for rel in candidates:
        p = Path(rel) if os.path.isabs(rel) else root_path / rel
        if p.is_file():
            try:
                ref = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {"path": rel, "status": "invalid", "problems": ["not_json"], "ref": None}
            problems = validate_parent_ref(ref)
            keep = {k: ref.get(k) for k in ("repository", "pull_number", "repository_id", "commit")} if isinstance(ref, dict) else None
            return {"path": rel, "status": "invalid" if problems else "ok", "problems": problems, "ref": keep}
    return {"path": None, "status": "missing", "problems": [], "ref": None}


def _first_parent_contains(repository, target, *, branch=None, transport="auto", timeout=30, max_pages=10):
    """True/False when target is (not) on the first-parent chain of the branch head,
    None when undetermined within max_pages of history (read-only API)."""
    r = repo(repository)
    if branch is None:
        info = request("GET", f"repos/{r}", transport=transport, timeout=timeout)
        branch = info.get("default_branch") if isinstance(info, dict) else None
        if not branch:
            return None
    parents, order, cursor = {}, {}, None
    for page in range(1, max_pages + 1):
        rows = request("GET", f"repos/{r}/commits?sha={urllib.parse.quote(branch, safe='')}&per_page=100&page={page}",
                       transport=transport, timeout=timeout)
        if not isinstance(rows, list) or not rows:
            return False if parents else None
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("sha"), str):
                parents[row["sha"]] = [p.get("sha") for p in row.get("parents") or [] if isinstance(p, dict)]
                order.setdefault(row["sha"], len(order))
        if cursor is None:
            cursor = rows[0].get("sha")
        while cursor in parents:
            if cursor == target:
                return True
            if target in order and order[cursor] > order[target]:
                return False  # the first-parent chain already went past the target (listing order)
            nxt = parents[cursor]
            if not nxt:
                return False
            cursor = nxt[0]
        if len(rows) < 100:
            return False
    return None


def verify_parent_ref(ref, *, transport="auto", timeout=30):
    """Check a parent-pr-ref/1 against GitHub: repository_id, and commit equals the PR
    merge_commit_sha (merged into the default branch) or head.sha (open)."""
    problems = validate_parent_ref(ref)
    out = {"status": "invalid", "problems": problems}
    if problems:
        return out
    r = repo(ref["repository"])
    try:
        info = request("GET", f"repos/{r}", transport=transport, timeout=timeout)
        pr = request("GET", f"repos/{r}/pulls/{ref['pull_number']}", transport=transport, timeout=timeout)
    except Error as e:
        return {"status": "unavailable", "problems": [e.code]}
    if not isinstance(info, dict) or info.get("id") != ref["repository_id"]:
        problems.append("repository_id_mismatch")
    if not isinstance(pr, dict):
        return {"status": "unavailable", "problems": ["invalid_pull"]}
    state = "merged" if pr.get("merged_at") else pr.get("state")
    expected = pr.get("merge_commit_sha") if state == "merged" else (pr.get("head") or {}).get("sha") if state == "open" else None
    if expected != ref["commit"]:
        problems.append("commit_mismatch")
    if state == "merged" and (pr.get("base") or {}).get("ref") != info.get("default_branch"):
        problems.append("not_merged_into_default_branch")
    if state not in ("merged", "open"):
        problems.append("pull_closed_unmerged")
    return {"status": "ok" if not problems else "mismatch", "problems": problems, "pull_state": state}


def load_child_heads(path):
    """{owner/name: sha} from the parent's child-heads.json (child-heads/1)."""
    data = _load_json_file(path, "heads")
    if not isinstance(data, dict) or not str(data.get("schema", "")).startswith("child-heads/") \
            or not isinstance(data.get("heads"), dict):
        raise ValueError("expected child-heads/1")
    out = {}
    for name, row in data["heads"].items():
        if not isinstance(row, dict) or not re.fullmatch(r"[0-9a-f]{40}", str(row.get("sha"))):
            raise ValueError("invalid head entry")
        out[repo(name)] = row["sha"]
    return out


def _public_targets(owner, repos, *, include_forks=False, transport="auto", timeout=30):
    """(targets, redacted): public, non-archived (non-fork) repositories. Anything else,
    including names that cannot be read, is counted in `redacted` and never named."""
    if repos is None:
        return owner_repositories(owner, include_forks=include_forks, transport=transport, timeout=timeout), 0
    targets, redacted = [], 0
    for name in repos:
        try:
            d = request("GET", f"repos/{repo(owner + '/' + name)}", transport=transport, timeout=timeout)
        except Error:
            redacted += 1
            continue
        visibility = d.get("visibility") or ("private" if d.get("private") else "public") if isinstance(d, dict) else None
        if not isinstance(d, dict) or visibility != "public" or d.get("private") or d.get("archived") \
                or (d.get("fork") and not include_forks):
            redacted += 1
            continue
        targets.append({"name": name, "default_branch": d.get("default_branch")})
    return targets, redacted


def enrollment_progress(owner, *, provider="myon-bioinformatics/gh_identity", source="gh_identity.py",
                        repos=None, heads=None, lock_paths=("vendor.lock.json", "tool/python/vendor.lock.json"),
                        include_forks=False, transport="auto", timeout=30, max_pages=10):
    """Per public consumer repository: is provider:source in its vendor lock, at which pin,
    is the pin stale, and is it on the provider's default-branch first-parent history."""
    provider = repo(provider)
    _vendor_path(source)
    targets, redacted = _public_targets(owner, repos, include_forks=include_forks, transport=transport, timeout=timeout)
    current = None
    try:
        info = request("GET", f"repos/{provider}", transport=transport, timeout=timeout)
        branch = info.get("default_branch") if isinstance(info, dict) else None
        head = request("GET", f"repos/{provider}/commits/{urllib.parse.quote(branch or 'HEAD', safe='')}", transport=transport, timeout=timeout)
        current = git_blob_sha(_remote_file(provider, source, head["sha"], transport, timeout))
    except (Error, KeyError, TypeError):
        branch = None
    first_parent_cache, rows, errors = {}, [], 0
    for t in targets:
        full = repo(f"{owner}/{t['name']}")
        if full == provider:
            continue
        ref = (heads or {}).get(full) or t["default_branch"] or "HEAD"
        try:
            sha, files, _ = _repo_tree(full, ref, transport, timeout)
            lp, lock = _repo_lock(full, sha, files, lock_paths, transport, timeout)
        except (Error, ValueError, json.JSONDecodeError):
            errors += 1
            continue
        entry = next((f for f in (lock or {"files": []})["files"]
                      if f["repository"] == provider and f["source"] == source), None)
        row = {"repository": full, "commit": sha, "lock": lp, "enrolled": entry is not None,
               "destination": None, "pin": None, "stale": None, "first_parent": None}
        if entry:
            pin = entry["commit"]
            if pin not in first_parent_cache:
                try:
                    first_parent_cache[pin] = _first_parent_contains(provider, pin, branch=branch, transport=transport,
                                                                     timeout=timeout, max_pages=max_pages) if branch else None
                except Error:
                    first_parent_cache[pin] = None
            row.update(destination=entry["destination"], pin=pin,
                       stale=None if current is None else entry["blob_sha"] != current,
                       first_parent=first_parent_cache[pin])
        rows.append(row)
    enrolled = [r for r in rows if r["enrolled"]]
    return {"schema": "gh-identity-enrollment-progress/1", "write": False, "owner": owner, "provider": provider,
            "source": source, "provider_blob": current, "repositories": rows,
            "counts": {"repositories": len(rows), "enrolled": len(enrolled), "not_enrolled": len(rows) - len(enrolled),
                       "stale": sum(bool(r["stale"]) for r in enrolled),
                       "not_first_parent": sum(r["first_parent"] is False for r in enrolled),
                       "first_parent_unknown": sum(r["first_parent"] is None for r in enrolled),
                       "errors": errors, "redacted": redacted}}


_PIN_USES = re.compile(r"uses:\s*['\"]?(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?P<path>/[^@\s'\"]*)?@(?P<sha>[0-9a-f]{40})\b")
_PIN_CHECKOUT_REPO = re.compile(r"^\s+repository:\s*['\"]?(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)['\"]?\s*$")
_PIN_CHECKOUT_REF = re.compile(r"^\s+ref:\s*['\"]?(?P<sha>[0-9a-f]{40})['\"]?\s*$")
PIN_JSON_GLOBS = ("vendor.lock.json", "**/vendor.lock.json", "**/publication.json", "**/parent-pr.ref.json",
                  "child-heads.json")


def _pin_repo(value):
    value = value.strip()
    if value.endswith(".git"):
        value = value[:-4]
    m = re.search(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)$", value.replace("https://github.com/", ""))
    if not m:
        raise ValueError("not an OWNER/REPO")
    return m.group(1)


def _workflow_pins(path, text):
    pins, lines = [], text.splitlines()
    for number, line in enumerate(lines, 1):
        m = _PIN_USES.search(line)
        if m:
            pins.append({"repository": m["repo"], "commit": m["sha"], "file": path, "line": number,
                         "entry": m["repo"] + (m["path"] or ""), "paths": []})
        r = _PIN_CHECKOUT_REPO.match(line)
        if r:
            for later in range(number, min(number + 12, len(lines))):
                ref = _PIN_CHECKOUT_REF.match(lines[later])
                if ref:
                    pins.append({"repository": r["repo"], "commit": ref["sha"], "file": path, "line": later + 1,
                                 "entry": "checkout " + r["repo"], "paths": []})
                    break
    return pins


def _json_pins(path, document):
    pins = []
    def walk(node, key=None):
        if isinstance(node, dict):
            commit = node.get("commit") or node.get("sha")
            repository = node.get("repository") or key
            if isinstance(repository, str) and isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit) \
                    and set(commit) != {"0"}:
                try:
                    name = _pin_repo(repository)
                except ValueError:
                    name = None
                if name:
                    source = node.get("source") or node.get("source_path")
                    pins.append({"repository": name, "commit": commit, "file": path, "line": None,
                                 "entry": node.get("destination") or node.get("path") or source or "",
                                 "paths": [source] if isinstance(source, str) else []})
            for k, value in node.items():
                walk(value, k if isinstance(k, str) and "/" in k else None)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(document)
    return pins


class PinInputError(ValueError):
    """An audit input could not be read or decoded; message uses checkout-relative paths."""


def collect_pins(root=".", json_globs=PIN_JSON_GLOBS):
    """Commit pins in a checkout: workflow `uses: OWNER/REPO@sha`, checkout `repository:`+`ref: sha`,
    and JSON objects with `repository`+`commit` (vendor.lock.json, parent-pr.ref.json, ...)."""
    root = os.path.abspath(os.fspath(root))
    pins = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "node_modules", ".venv", "__pycache__", ".test-deps"))
        for name in sorted(files):
            full = os.path.join(directory, name)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            try:
                if rel.startswith(".github/workflows/") and name.endswith((".yml", ".yaml")):
                    with open(full, encoding="utf-8") as stream:
                        pins += _workflow_pins(rel, stream.read(4 * 1024 * 1024))
                elif name.endswith(".json") and any(fnmatch.fnmatch(rel, g) for g in json_globs):
                    with open(full, encoding="utf-8") as stream:
                        pins += _json_pins(rel, json.loads(stream.read(8 * 1024 * 1024)))
            except (OSError, ValueError) as error:
                reason = "read_error" if isinstance(error, OSError) else "invalid_json_or_encoding"
                raise PinInputError(f"{rel}: {reason}") from error
    return pins


def _audit_repository(repository, items, remote, timeout):
    """Check all pins of one repository in a temporary bare repository (blob-less full history).
    Git never receives credentials: credential helpers are disabled for these calls."""
    nocred = ["-c", "credential.helper=", "-c", "http.extraHeader="]
    with tempfile.TemporaryDirectory(prefix="ghi-pin-audit-") as bare:
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True, capture_output=True, timeout=60)
        def git(*args, ok=(0,)):
            raw, _, code = _git_run(bare, [*nocred, *args], max_bytes=64 * 1024 * 1024, ok=ok, timeout=timeout)
            return _git_decode(raw), code
        head, _ = git("ls-remote", "--symref", "--", remote, "HEAD")
        m = re.search(r"ref: refs/heads/(\S+)\s+HEAD", head)
        branch = m.group(1) if m else "main"
        git("fetch", "-q", "--no-tags", "--filter=blob:none", "--", remote, f"+refs/heads/{branch}:refs/heads/{branch}")
        first_parent = git("rev-list", "--first-parent", branch)[0].split()
        tip = first_parent[0] if first_parent else None
        on_line = set(first_parent)
        rows = []
        for commit in sorted({p["commit"] for p in items}):
            pins = [p for p in items if p["commit"] == commit]
            row = {"repository": repository, "commit": commit, "branch": branch, "pins": pins, "final": None}
            if git("cat-file", "-e", commit + "^{commit}", ok=(0, 1, 128))[1] != 0:
                if git("fetch", "-q", "--no-tags", "--filter=blob:none", "--", remote, commit, ok=(0, 1, 128))[1] != 0:
                    rows.append({**row, "state": "unmerged", "detail": "commit not found on the remote"})
                    continue
            if git("merge-base", "--is-ancestor", commit, branch, ok=(0, 1))[1] != 0:
                rows.append({**row, "state": "unmerged", "detail": f"not reachable from {branch}"})
                continue
            since = int(git("rev-list", "--count", f"{commit}..{branch}")[0].strip() or 0)
            if commit not in on_line:
                descendants = set(git("rev-list", "--ancestry-path", f"{commit}..{branch}")[0].split())
                merge = next((c for c in reversed(first_parent) if c in descendants), tip)
                rows.append({**row, "state": "not_final", "final": merge, "since": since,
                             "detail": "merged; pin the first-parent merge commit instead"})
                continue
            paths = sorted({p for pin in pins for p in pin["paths"] if isinstance(p, str)})
            changed = git("diff", "--name-only", commit, branch, "--", *paths)[0].split() if paths else []
            rows.append({**row, "state": "drift" if changed else "ok", "since": since, "changed": changed[:20],
                         "detail": f"{len(changed)} pinned file(s) changed on {branch}" if changed else f"+{since} on {branch}"})
        return rows


def pin_audit(root=".", *, json_globs=PIN_JSON_GLOBS, only_owners=None, remote_base="https://github.com/", timeout=300):
    """Audit commit pins of a checkout with plain git against public remotes (no API token).

    States: ok, drift (pinned files changed on the default branch), not_final
    (merged but not on the default branch first-parent line: a PR head was
    pinned; `final` is the merge commit to pin), unmerged, unavailable. A
    repository that cannot be fetched without credentials is reported without
    its name (it may be private).
    """
    if not isinstance(remote_base, str) or not remote_base.startswith(("https://", "file://", "/")):
        raise ValueError("remote_base must be https://, file:// or an absolute path")
    pins = collect_pins(root, tuple(json_globs))
    if only_owners:
        pins = [p for p in pins if p["repository"].split("/")[0] in set(only_owners)]
    by_repo = {}
    for p in pins:
        by_repo.setdefault(p["repository"], []).append(p)
    rows, redacted = [], 0
    for repository in sorted(by_repo):
        try:
            rows += _audit_repository(repository, by_repo[repository], remote_base + repository, timeout)
        except (GitInspectionError, subprocess.SubprocessError, OSError):
            redacted += 1
            for p in by_repo[repository]:
                rows.append({"repository": None, "commit": None, "branch": None, "final": None, "state": "unavailable",
                             "pins": [{"file": p["file"], "line": p["line"]}], "detail": "remote not readable without credentials"})
    counts = dict(Counter(r["state"] for r in rows))
    return {"schema": "gh-identity-pin-audit/1", "write": False, "pins": len(pins), "results": rows,
            "counts": counts, "redacted": redacted, "ok": all(r["state"] == "ok" for r in rows)}


def render_pin_audit(o):
    lines = []
    for r in o["results"]:
        where = sorted({p["file"] + (f":{p['line']}" if p.get("line") else "") for p in r["pins"]})
        more = f" +{len(where) - 2} more" if len(where) > 2 else ""
        name = f"{r['repository']}@{r['commit'][:7]}" if r["repository"] else "(redacted)"
        final = f" -> {r['final']}" if r.get("final") else ""
        lines.append(f"{r['state']} {name}{final} {r['detail']}  [{len(r['pins'])} pin(s): {', '.join(where[:2])}{more}]")
    lines.append(f"pins={o['pins']} " + " ".join(f"{k}={v}" for k, v in sorted(o["counts"].items())))
    return "\n".join(lines)


_REQUEST = contextvars.ContextVar("ghi_request", default=None)


@contextlib.contextmanager
def request_stub(handler):
    """Temporarily replace GitHub `request` for tests (stdlib ContextVar)."""
    token = _REQUEST.set(handler)
    try:
        yield
    finally:
        _REQUEST.reset(token)


def repo_identity(repository, *, transport="auto", timeout=30):
    """Repository identity (public, including archived): id, visibility, archived, fork, default branch, head SHA.

    Non-public repositories raise Error(not_public). Read-only.
    """
    r = repo(repository)
    d = request("GET", f"repos/{r}", transport=transport, timeout=timeout)
    if not isinstance(d, dict):
        raise Error("invalid_repository")
    visibility = d.get("visibility") or ("private" if d.get("private") else "public")
    if visibility != "public" or d.get("private"):
        raise Error("not_public")
    branch = d.get("default_branch")
    if not isinstance(branch, str) or not branch:
        raise Error("no_default_branch")
    head = request("GET", f"repos/{r}/commits/{urllib.parse.quote(branch, safe='')}", transport=transport, timeout=timeout)
    if not isinstance(head, dict) or not re.fullmatch(r"[0-9a-f]{40}", str(head.get("sha"))):
        raise Error("invalid_commit")
    return {"schema": "gh-identity-repo-identity/1", "write": False, "repository": r, "repository_id": d.get("id"),
            "visibility": visibility, "archived": bool(d.get("archived")), "fork": bool(d.get("fork")),
            "default_branch": branch, "head": head["sha"], "owner_type": (d.get("owner") or {}).get("type")}


def list_owner_repositories(owner, *, kind="auto", include_forks=False, include_archived=False,
                            transport="auto", timeout=30, max_pages=20):
    """List repositories for a user or organization (public only by default).

    kind: auto (try /users then /orgs), user, or org. No hard-coded names.
    At most max_pages pages are fetched; `complete` is False (truncated_reason
    "max_pages") when more pages remained.
    """
    if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", owner):
        raise ValueError("invalid owner")
    if kind not in ("auto", "user", "org"):
        raise ValueError("kind must be auto, user or org")
    bases = {"user": [f"users/{owner}/repos?type=owner&sort=full_name"],
             "org": [f"orgs/{owner}/repos?type=all&sort=full_name"],
             "auto": [f"users/{owner}/repos?type=owner&sort=full_name", f"orgs/{owner}/repos?type=all&sort=full_name"]}[kind]
    rows, source, seen = [], None, set()
    complete, fetched = True, 0
    for base in bases:
        try:
            page, complete, fetched = pages_bounded(base, transport, timeout, max_pages=max_pages)
        except Error as e:
            if e.code in _NOT_FOUND_CODES and kind == "auto":
                continue
            raise
        source = base.split("/", 1)[0]
        for x in page:
            if not isinstance(x, dict) or not isinstance(x.get("full_name"), str):
                continue
            if x["full_name"] in seen:
                continue
            visibility = x.get("visibility") or ("private" if x.get("private") else "public")
            if visibility != "public" or x.get("private"):
                continue
            if x.get("archived") and not include_archived:
                continue
            if x.get("fork") and not include_forks:
                continue
            seen.add(x["full_name"])
            rows.append({"full_name": x["full_name"], "repository_id": x.get("id"), "default_branch": x.get("default_branch"),
                         "archived": bool(x.get("archived")), "fork": bool(x.get("fork"))})
        if rows or kind != "auto":
            break
    return {"schema": "gh-identity-owner-repositories/1", "write": False, "owner": owner, "kind": source or kind,
            "count": len(rows), "complete": complete, "pages_fetched": fetched,
            "truncated_reason": None if complete else "max_pages", "repositories": rows}


def pr_identity(repository, number, *, transport="auto", timeout=30):
    """PR identity: state, head/base SHAs and refs, merge_commit_sha, repository_id."""
    if type(number) is not int or number < 1:
        raise ValueError("number must be positive")
    r = repo(repository)
    info = request("GET", f"repos/{r}", transport=transport, timeout=timeout)
    pr = request("GET", f"repos/{r}/pulls/{number}", transport=transport, timeout=timeout)
    if not isinstance(info, dict) or not isinstance(pr, dict):
        raise Error("invalid_pull")
    state = "merged" if pr.get("merged_at") else pr.get("state")
    head, base = pr.get("head") or {}, pr.get("base") or {}
    return {"schema": "gh-identity-pr-identity/1", "write": False, "repository": r, "repository_id": info.get("id"),
            "number": number, "state": state, "merged_at": pr.get("merged_at"),
            "merge_commit_sha": pr.get("merge_commit_sha") if state == "merged" else None,
            "head": {"sha": head.get("sha"), "ref": head.get("ref")},
            "base": {"sha": base.get("sha"), "ref": base.get("ref")},
            "default_branch": info.get("default_branch"),
            "merged_into_default": state == "merged" and base.get("ref") == info.get("default_branch")}


def commit_pulls(repository, sha, *, transport="auto", timeout=30):
    """Pull requests associated with a commit (GitHub /commits/{sha}/pulls). Read-only."""
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("sha must be 40 hex")
    r = repo(repository)
    rows = request("GET", f"repos/{r}/commits/{sha}/pulls?per_page=100", transport=transport, timeout=timeout)
    if not isinstance(rows, list):
        raise Error("invalid_pulls")
    pulls = []
    for p in rows:
        if not isinstance(p, dict):
            continue
        pulls.append({"number": p.get("number"), "state": "merged" if p.get("merged_at") else p.get("state"),
                      "merged_at": p.get("merged_at"), "merge_commit_sha": p.get("merge_commit_sha"),
                      "base_ref": (p.get("base") or {}).get("ref"), "head_sha": (p.get("head") or {}).get("sha")})
    return {"schema": "gh-identity-commit-pulls/1", "write": False, "repository": r, "sha": sha, "count": len(pulls),
            "pulls": pulls}


_GIT_NO_CREDENTIALS = ("-c", "credential.helper=", "-c", "core.askPass=", "-c", "http.extraHeader=")


def _default_git_runner(args, cwd, timeout=300):
    """Run `git <args>` in cwd with gh_identity's sanitized git (no shell). Returns (code, stdout)."""
    raw, _, code = _git_run(cwd, list(args), max_bytes=64 * 1024 * 1024, ok=tuple(range(256)), timeout=timeout)
    return code, raw


def _validate_remote(remote):
    if not isinstance(remote, str) or not remote or "\n" in remote or remote.startswith("-"):
        raise ValueError("invalid remote")
    if remote.startswith("/"):
        return remote
    parts = urllib.parse.urlsplit(remote)
    if parts.scheme not in ("https", "file"):
        raise ValueError("remote must be https://, file:// or an absolute path")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ValueError("remote must not carry credentials")
    return remote


def first_parent_check(repository, commit, *, branch=None, exact=False, transport="auto", timeout=30,
                       max_pages=10, remote_base="https://github.com/", remote=None, git_runner=None):
    """Whether `commit` is on the default-branch (or `branch`) first-parent history.

    exact=False uses the commits API (may return None when history is longer than
    max_pages). exact=True uses a temporary bare fetch with --filter=blob:none and
    credential helpers disabled; True/False only.

    remote: full remote URL/path (default remote_base + repository). URLs carrying
    credentials are rejected; tokens never appear in git arguments.
    git_runner(args, cwd) -> (returncode, stdout_bytes): replaces how git is run
    (e.g. a caller's own sanitized subprocess, or an offline test double).
    Raises Error("git_remote_unavailable") when the remote or branch cannot be fetched.
    """
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("commit must be 40 hex")
    r = repo(repository)
    if not exact:
        result = _first_parent_contains(r, commit, branch=branch, transport=transport, timeout=timeout, max_pages=max_pages)
        return {"schema": "gh-identity-first-parent/1", "write": False, "repository": r, "commit": commit,
                "branch": branch, "exact": False, "contains": result, "reason": None if result is not None else "history_truncated"}
    if remote is None:
        if not isinstance(remote_base, str) or not remote_base.startswith(("https://", "file://", "/")):
            raise ValueError("remote_base must be https://, file:// or an absolute path")
        remote = remote_base + r
    remote = _validate_remote(remote)
    if branch is not None and (not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9._/-]+", branch) or branch.startswith("-")):
        raise ValueError("invalid branch")
    if git_runner is None:
        git_runner = lambda args, cwd: _default_git_runner(args, cwd, timeout=timeout)
    if not callable(git_runner):
        raise TypeError("git_runner must be callable")
    def result(contains, reason, branch):
        return {"schema": "gh-identity-first-parent/1", "write": False, "repository": r, "commit": commit,
                "branch": branch, "exact": True, "contains": contains, "reason": reason}
    with tempfile.TemporaryDirectory(prefix="ghi-fp-") as bare:
        def git(*args):
            code, out = git_runner([*_GIT_NO_CREDENTIALS, *args], bare)
            return code, (out or b"").decode("utf-8", "replace")
        if git("init", "-q", "--bare")[0] != 0:
            raise Error("git_failed")
        code, head = git("ls-remote", "--symref", "--", remote, "HEAD")
        if code != 0:
            raise Error("git_remote_unavailable")
        m = re.search(r"ref: refs/heads/(\S+)\s+HEAD", head)
        branch = branch or (m.group(1) if m else "main")
        if git("fetch", "-q", "--no-tags", "--filter=blob:none", "--", remote,
               f"+refs/heads/{branch}:refs/heads/{branch}")[0] != 0:
            raise Error("git_remote_unavailable")
        if git("cat-file", "-e", commit + "^{commit}")[0] != 0:
            if git("fetch", "-q", "--no-tags", "--filter=blob:none", "--", remote, commit)[0] != 0:
                return result(False, "commit_not_found", branch)
        code, listed = git("rev-list", "--first-parent", f"refs/heads/{branch}")
        if code != 0:
            raise Error("git_failed")
        return result(commit in set(listed.split()), None, branch)

def local_position(root="."):
    """Where am I: cwd, Git top level, checkout HEAD, change counts, GitHub remote.

    Local only (no network). Only an explicit Git answer becomes an absence:
    not_a_checkout (exit 128 and no .git above cwd) or no_commit (HEAD does not
    verify). Anything else (no git executable, permissions, a broken .git,
    output bounds) is git_error with a short detail, never an absence.
    """
    cwd = os.path.abspath(os.fspath(root))
    base = {"schema": "gh-identity-local-position/1", "cwd": cwd}
    def failed(error):
        return {**base, "git": None, "reason": "git_error", "detail": str(error)}
    try:
        raw, _, code = _git_run(root, ["rev-parse", "--show-toplevel"], max_bytes=64 * 1024, ok=(0, 128))
        if code == 128:
            here = Path(cwd)
            if any((folder / ".git").exists() for folder in (here, *here.parents)):
                return failed("rev-parse failed inside a .git tree")
            return {**base, "git": None, "reason": "not_a_checkout"}
        top = _git_decode(raw).strip()
        if _git_run(root, ["rev-parse", "--verify", "-q", "HEAD^{commit}"], max_bytes=4096, ok=(0, 1))[2] == 1:
            return {**base, "git": None, "reason": "no_commit"}
        head = checkout_head(root)
        status = git_status(root)
        remote = remote_repository(root)
    except (GitInspectionError, ValueError) as error:
        return failed(error)
    kinds = Counter()
    for record in status["records"]:
        if record["kind"] == "?":
            kinds["untracked"] += 1
        elif record["kind"] in ("1", "2", "u"):
            xy = record["record"].split(" ", 2)[1]
            kinds["staged"] += xy[0] != "."
            kinds["unstaged"] += xy[1] != "."
    return {**base,
            "git": {"top_level": top, "relative": os.path.relpath(cwd, top), "head": head,
                    "changes": {k: kinds.get(k, 0) for k in ("staged", "unstaged", "untracked")},
                    "changes_truncated": status["truncated"], "remote": remote}}


def repository_metadata(root=".", *, repository, source_sha=None, expected_sha=None, generated_at=None):
    """Minimal public record: repository, checkout HEAD, optional source commit.

    source_sha is the caller-stated origin of the change (for example a PR head)
    and is reported separately; it never replaces the checkout HEAD.
    """
    head = checkout_head(root)
    if expected_sha is not None and head["sha"] != _rm_sha(expected_sha, "expected_sha"):
        raise ValueError("checkout HEAD does not match expected_sha")
    record = {"schema_version": REPOSITORY_METADATA_SCHEMA,
              "repository": {"full_name": _rm_full_name(repository)},
              "head": head,
              "source": {"sha": _rm_sha(source_sha, "source_sha") if source_sha else None},
              "generated_at": _rm_time(generated_at or now().replace("+00:00", "Z"), "generated_at")}
    return record


def tool_versions(commands=(), *, include_python=False, timeout=5.0):
    """Explicitly requested, allowlisted tool versions; missing tools are omitted."""
    if isinstance(commands, (str, bytes)):
        raise TypeError("commands must be a sequence of command names")
    unknown = [command for command in commands if command not in _RM_TOOL_ARGS]
    if unknown:
        raise ValueError("unsupported tooling command: " + ", ".join(map(str, unknown)))
    observed = {}
    if include_python:
        import platform
        match = _RM_TOOL_PATTERNS["python"].fullmatch(platform.python_version())
        if match:
            observed["python"] = match.group("v")
    for command in commands:
        executable = shutil.which(command)
        if not executable:
            continue
        try:
            result = subprocess.run([executable, _RM_TOOL_ARGS[command]], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        text = (result.stdout.strip() or result.stderr.strip()) if result.returncode == 0 else ""
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        match = _RM_TOOL_PATTERNS[command].fullmatch(first)
        if match and len(match.group("v")) <= 32:
            observed[command] = match.group("v")
    return observed


def validate_repository_metadata_v1(record):
    """The Ironmate repository-metadata v1 contract, field for field."""
    if not isinstance(record, dict) or set(record) != {"schema_version", "repository", "head", "measurements", "tooling", "generated_at"}:
        raise ValueError("unexpected or missing top-level fields")
    if record["schema_version"] != REPOSITORY_METADATA_V1_SCHEMA:
        raise ValueError("unsupported schema_version")
    head = record["head"]
    if not isinstance(head, dict) or set(head) != {"sha", "short_sha", "branch", "timestamp", "subject"}:
        raise ValueError("invalid head")
    if not isinstance(head["sha"], str) or not _RM_SHA.fullmatch(head["sha"]):
        raise ValueError("sha must be lowercase full hex")
    if head["short_sha"] != head["sha"][:8]:
        raise ValueError("short_sha must be the first 8 characters of sha")
    if not head["branch"] or not head["subject"]:
        raise ValueError("branch and subject are required")
    _rm_time(head["timestamp"], "timestamp")
    _rm_time(record["generated_at"], "generated_at")
    if not isinstance(record["repository"], dict) or set(record["repository"]) != {"full_name"}:
        raise ValueError("invalid repository")
    _rm_full_name(record["repository"]["full_name"])
    sizes = record["measurements"]
    if not isinstance(sizes, dict) or set(sizes) != set(_RM_MEASUREMENTS) or any(
            value is not None and (type(value) is not int or value < 0) for value in sizes.values()):
        raise ValueError("measurements must be non-negative integers or null")
    tooling = record["tooling"]
    if not isinstance(tooling, dict) or any(
            not isinstance(k, str) or not _RM_TOOL_NAME.fullmatch(k) or
            not isinstance(v, str) or not _RM_TOOL_VALUE.fullmatch(v) for k, v in tooling.items()):
        raise ValueError("tooling values must be short version labels")
    return record


def repository_metadata_v1(root=".", *, repository, env=None, expected_sha=None, generated_at=None,
                           tooling=None, measurements=None):
    """Compatibility adapter producing the existing schema v1 record.

    Branch keeps v1's label rule: GITHUB_HEAD_REF, then GITHUB_REF_NAME, then
    the checkout branch, else "detached". The SHA is always the checkout HEAD.
    """
    env = os.environ if env is None else env
    record = repository_metadata(root, repository=repository, expected_sha=expected_sha, generated_at=generated_at)
    head = record["head"]
    branch = ((env.get("GITHUB_HEAD_REF") or "").strip() or (env.get("GITHUB_REF_NAME") or "").strip()
              or head["branch"] or "detached")
    sizes = dict.fromkeys(_RM_MEASUREMENTS)
    extra = set(measurements or {}) - set(_RM_MEASUREMENTS)
    if extra:
        raise ValueError("unknown measurements: " + ", ".join(sorted(extra)))
    sizes.update(measurements or {})
    return validate_repository_metadata_v1({
        "schema_version": REPOSITORY_METADATA_V1_SCHEMA,
        "repository": record["repository"],
        "head": {"sha": head["sha"], "short_sha": head["sha"][:8], "branch": branch,
                 "timestamp": head["timestamp"], "subject": head["subject"]},
        "measurements": sizes, "tooling": dict(tooling or {}),
        "generated_at": record["generated_at"]})


def write_repository_metadata(record, output_dir):
    """repository-metadata.json (pretty) and .jsonl (one line), sorted keys."""
    if record.get("schema_version") == REPOSITORY_METADATA_V1_SCHEMA:
        validate_repository_metadata_v1(record)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    pretty = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    (output / "repository-metadata.json").write_text(pretty, encoding="utf-8")
    (output / "repository-metadata.jsonl").write_text(line, encoding="utf-8")
    return {"json": "repository-metadata.json", "jsonl": "repository-metadata.jsonl"}


def overview():
 """Best-effort read-only snapshot; unknown is never confused with zero."""
 out={"schema":"gh-identity-overview/1","environment":{"python":sys.version.split()[0],
      "git_version":None,"gh_version":None},"local":None,"remote":None,
      "pull_requests":None,"issues":None,"ci":None,
      "next_actions":["git-status","local-position","prs OWNER/REPO","issues OWNER/REPO","pr-status OWNER/REPO NUMBER","checks OWNER/REPO SHA"]}
 try:
  versions=tool_versions(("git","gh"))
  out["environment"].update({"git_version":versions.get("git"),"gh_version":versions.get("gh")})
 except (OSError,ValueError,Error,subprocess.SubprocessError):pass
 try:
  position=local_position()
  # Allowlist public fields; local-position remains the explicit path-bearing API.
  out["local"]={"git":None}
  if "reason" in position:out["local"]["reason"]=position["reason"]
  git=position.get("git")
  if isinstance(git,dict):
   public={}
   for field,keys in (("head",("sha","branch","detached","timestamp")),
                      ("changes",("staged","unstaged","untracked")),
                      ("remote",("remote","full_name","reason"))):
    value=git.get(field)
    if isinstance(value,dict):public[field]={key:value[key] for key in keys if key in value}
   if "changes_truncated" in git:public["changes_truncated"]=git["changes_truncated"]
   out["local"]["git"]=public
 except (OSError,ValueError,Error,GitInspectionError):pass
 local=out["local"] or {}
 git=local.get("git") if isinstance(local,dict) else None
 remote=git.get("remote") if isinstance(git,dict) else None
 # Remote discovery is intentionally optional: never guess the repository from cwd.
 name=remote.get("full_name") if isinstance(remote,dict) else None
 if not isinstance(name,str) or not REPO.fullmatch(name):return out
 out["remote"]={"repository":name}
 for key,fn in (("pull_requests",lambda:pull_requests(name,max_items=5,max_pages=1)),
                ("issues",lambda:issues(name,max_items=5,max_pages=1)),
                ("ci",lambda:run_history(name,max_items=5,max_pages=1))):
  try:out[key]=fn()
  except (OSError,ValueError,Error,GitInspectionError):pass
 return out

def _main(argv=None):
 argv=list(sys.argv[1:] if argv is None else argv)
 if not argv:argv=["overview"]
 transport="auto"
 if "--transport" in argv:
  i=argv.index("--transport")
  if i+1>=len(argv): print(json.dumps({"status":"error","error":"invalid_argument"}),file=sys.stderr);return 2
  transport=argv[i+1]
  if transport not in ("auto","gh","urllib"): print(json.dumps({"status":"error","error":"invalid_argument"}),file=sys.stderr);return 2
  del argv[i:i+2]
 a=argparse.ArgumentParser(epilog="Global options: --transport auto|gh|urllib, --max-pages N (100), --max-items N (10000), --max-bytes N (10000000), --timeout SECONDS (30). Limits are cumulative per operation.");s=a.add_subparsers(dest="cmd",required=True)
 x=s.add_parser("html-content");x.add_argument("file");x.add_argument("--selector",action="append",required=True);x.add_argument("--source-kind",choices=("html","dom"),default="html");x.add_argument("--input-bytes",type=int,default=10_000_000);x.add_argument("--include-controls",action="store_true")
 s.add_parser("capabilities")
 s.add_parser("overview",help="best-effort local and GitHub workspace summary (default)")
 for command in ("git-status", "git-files", "git-diff", "git-log", "git-numstat", "git-show", "git-blame", "git-grep", "git-ignore"):
  x=s.add_parser(command);x.add_argument("--root",default=".");x.add_argument("--output-bytes",type=int,default=1000000)
  if command=="git-status":x.add_argument("--json",action="store_true",help="structured output instead of default short status")
  if command in ("git-diff", "git-blame"):x.add_argument("--path",required=command=="git-blame")
  if command=="git-diff":x.add_argument("--staged",action="store_true");x.add_argument("--base");x.add_argument("--head")
  if command in ("git-log", "git-numstat"):x.add_argument("--count",type=int,default=50)
  if command=="git-show":x.add_argument("revision",nargs="?",default="HEAD");x.add_argument("--path")
  if command=="git-grep":x.add_argument("pattern")
  if command=="git-ignore":x.add_argument("paths",nargs="+")
 x=s.add_parser("git-work",help="work branch from a PR head (#N) or remote branch, with branch.<name>.remote/merge set (planned unless --write)");x.add_argument("--root",default=".");x.add_argument("source",metavar="#N|pr/N|BRANCH");x.add_argument("branch",metavar="WORK_BRANCH");x.add_argument("--upstream",metavar="REMOTE_BRANCH");x.add_argument("--remote",default="origin");x.add_argument("--depth",type=int);x.add_argument("--fetch-timeout",type=float,default=120);x.add_argument("--write",action="store_true")
 x=s.add_parser("git-switch");x.add_argument("--root",default=".");x.add_argument("branch",nargs="?",help='existing branch, or "-" for the previous branch');x.add_argument("-c","--create",metavar="NEW_BRANCH");x.add_argument("--write",action="store_true")
 x=s.add_parser("local-position",help="cwd, Git top level, checkout HEAD, change counts, GitHub remote (local only)");x.add_argument("--root",default=".")
 x=s.add_parser("subtree-split",help="read-only git subtree split --prefix: print the split commit SHA (no refs changed)");x.add_argument("--root",default=".");x.add_argument("--prefix",required=True);x.add_argument("--revision",default="HEAD");x.add_argument("--timeout",type=float,default=600)
 x=s.add_parser("subtree-sync-status",help="compare subtree split of a monorepo with a split-out checkout (local, read-only)");x.add_argument("--root",default=".");x.add_argument("--prefix",required=True);x.add_argument("--revision",default="HEAD");x.add_argument("--other-root",required=True);x.add_argument("--other-ref",default="HEAD");x.add_argument("--other-only-path",action="append",default=[],metavar="GLOB");x.add_argument("--mainline",help="also check the source is on this ref's first-parent history");x.add_argument("--timeout",type=float,default=600)
 x=s.add_parser("sync-provenance",help="record or verify which monorepo SHA a subtree sync came from (read-only)");x.add_argument("--root",default=".");x.add_argument("--prefix");x.add_argument("--revision",default="HEAD");x.add_argument("--other-root");x.add_argument("--other-ref",default="HEAD");x.add_argument("--other-only-path",action="append",default=[],metavar="GLOB");x.add_argument("--direction",choices=("push","pull"));x.add_argument("--squash",action="store_true");x.add_argument("--generated-at");x.add_argument("--output-dir");x.add_argument("--verify",metavar="COMMIT",help="check Subtree-Split trailers of COMMIT in --root");x.add_argument("--monorepo-root",help="with --verify: recompute the split there");x.add_argument("--timeout",type=float,default=600)
 x=s.add_parser("split-preflight",help="read-only checklist before splitting a directory into its own repository");x.add_argument("--root",default=".");x.add_argument("--prefix",required=True);x.add_argument("--revision",default="HEAD");x.add_argument("--timeout",type=float,default=600)
 x=s.add_parser("vendor-lock-check",help="compare vendor.lock.json (vendor-lock/1) with files on disk; optional vendor-rules/1 (local, read-only)");x.add_argument("--root",default=".");x.add_argument("--lock",default="vendor.lock.json");x.add_argument("--rules");x.add_argument("--parent-ref",help="parent-pr.ref.json path (default: templates/ then root; required when given)");x.add_argument("--ci-check",action="store_true",help="CI gate: exit 1 unless ok");x.add_argument("--markdown",action="store_true",help="Markdown table (for GITHUB_STEP_SUMMARY) instead of JSON")
 x=s.add_parser("vendor-lock-verify-remote",help="check vendor.lock.json entries against upstream repository@commit (read-only)");x.add_argument("--root",default=".");x.add_argument("--lock",default="vendor.lock.json");x.add_argument("--upstream",action="store_true",help="also report the latest upstream commit touching each source");x.add_argument("--first-parent",action="store_true",help="pins must be on the source default branch first-parent history");x.add_argument("--check-parent-ref",action="store_true",help="verify parent-pr.ref.json repository_id and PR merge/head commit");x.add_argument("--parent-ref");x.add_argument("--ci-check",action="store_true",help="CI gate: exit 1 unless ok (with --upstream, also when upstream moved)");x.add_argument("--markdown",action="store_true",help="Markdown table (for GITHUB_STEP_SUMMARY) instead of JSON")
 x=s.add_parser("vendor-plan",help="rule-based vendor placement plan for OWNER/REPO:PATH@SHA candidates (read-only)");x.add_argument("--root",default=".");x.add_argument("--rules");x.add_argument("--candidate",action="append",required=True,metavar="OWNER/REPO:PATH@SHA")
 x=s.add_parser("vendor-matrix",help="who vendors whom at which commit across repositories (read-only)");x.add_argument("owner");x.add_argument("--repos",help="comma-separated repository names (default: all non-archived)");x.add_argument("--lock-path",action="append",metavar="PATH");x.add_argument("--heads",help="child-heads.json: read locks at the recorded commits")
 x=s.add_parser("enrollment-progress",help="per public consumer: provider file in vendor lock, pin, stale, first-parent (read-only)");x.add_argument("--owner",required=True);x.add_argument("--provider",default="myon-bioinformatics/gh_identity");x.add_argument("--source",default="gh_identity.py");x.add_argument("--repos");x.add_argument("--heads");x.add_argument("--include-forks",action="store_true")
 x=s.add_parser("duplicate-files",help="same basename across an owner's repositories, with vendor-lock provenance (read-only)");x.add_argument("--owner",required=True);x.add_argument("--repos",help="comma-separated names (default: public, non-archived, non-fork repos owned by --owner)");x.add_argument("--ignore",action="append",default=[],metavar="GLOB",help="additional basename/path patterns to ignore");x.add_argument("--no-default-ignore",action="store_true");x.add_argument("--include-forks",action="store_true");x.add_argument("--min-repos",type=int,default=2);x.add_argument("--heads",help="child-heads.json: scan the recorded commits");x.add_argument("--markdown",action="store_true",help="Markdown summary (for GITHUB_STEP_SUMMARY) instead of JSON");x.add_argument("--top",type=int,default=30,help="groups shown with --markdown")
 x=s.add_parser("pin-audit",help="audit commit pins (workflows, vendor locks, parent-pr.ref.json) with plain git: ok/drift/not_final/unmerged (read-only, no token)");x.add_argument("--root",default=".");x.add_argument("--json",action="append",dest="json_globs",metavar="GLOB");x.add_argument("--only",action="append",metavar="OWNER");x.add_argument("--remote-base",default="https://github.com/");x.add_argument("--git-timeout",type=float,default=300);x.add_argument("--text",action="store_true",help="one line per pinned commit")
 x=s.add_parser("repo-identity",help="public repository id, visibility, default branch, head SHA (read-only)");x.add_argument("repository")
 x=s.add_parser("owner-repositories",help="public repositories of a user or org (read-only; no hard-coded names)");x.add_argument("owner");x.add_argument("--kind",choices=("auto","user","org"),default="auto");x.add_argument("--include-forks",action="store_true");x.add_argument("--include-archived",action="store_true")
 x=s.add_parser("pr-identity",help="PR state, head/base SHAs, merge_commit_sha, repository_id (read-only)");x.add_argument("repository");x.add_argument("number",type=int)
 x=s.add_parser("commit-pulls",help="pull requests associated with a commit (read-only)");x.add_argument("repository");x.add_argument("sha")
 x=s.add_parser("first-parent",help="is commit on the default-branch first-parent history (API or exact git)");x.add_argument("repository");x.add_argument("commit");x.add_argument("--branch");x.add_argument("--exact",action="store_true",help="blob-less bare fetch (True/False only)");x.add_argument("--remote-base",default="https://github.com/");x.add_argument("--git-timeout",type=float,default=300)
 x=s.add_parser("repo-metadata",help="checkout repository metadata (v2 minimal, or v1 compatibility)");x.add_argument("--root",default=".");x.add_argument("--repository",default=os.environ.get("GITHUB_REPOSITORY"));x.add_argument("--format",choices=("v2","v1"),default="v2");x.add_argument("--source-sha");x.add_argument("--expected-sha");x.add_argument("--generated-at");x.add_argument("--tool",action="append",default=[],metavar="NAME",help="v1 only: observe an allowlisted tool version (python, git, gh, node, npm, npx)");x.add_argument("--output-dir")
 x=s.add_parser("repo");x.add_argument("repo")
 x=s.add_parser("tree");x.add_argument("repo");x.add_argument("--ref",default="main")
 depth_args=x.add_mutually_exclusive_group();depth_args.add_argument("--depth",type=int,default=1);depth_args.add_argument("--recursive",action="store_true")
 x.add_argument("--exclude-dir",action="append",default=[]);x.add_argument("--exclude-file",action="append",default=[])
 x=s.add_parser("repos");x.add_argument("owner")
 x=s.add_parser("repo-inventory");x.add_argument("owner");x.add_argument("--fields",default="name");x.add_argument("--sort",choices=("name","size-kb","run-count","updated-at"),default="name");x.add_argument("--order",choices=("asc","desc"),default="asc");x.add_argument("--max-repos",type=int,default=30);x.add_argument("--page-limit",type=int,default=5)
 x=s.add_parser("issue");x.add_argument("repo");x.add_argument("number",type=int)
 x=s.add_parser("content");x.add_argument("repo");x.add_argument("kind",choices=("pr","issue","commit"));x.add_argument("identifier");x.add_argument("--field",action="append")
 x=s.add_parser("issues");x.add_argument("repo");x.add_argument("--state",choices=("open","closed","all"),default="open");x.add_argument("--limit",type=int,default=100);x.add_argument("--page-limit",type=int,default=10);x.add_argument("--text",action="store_true")
 x=s.add_parser("search");x.add_argument("query");x.add_argument("--kind",choices=("pr","issue"),default="pr");x.add_argument("--sort",choices=("updated","created","comments","best-match"),default="updated");x.add_argument("--order",choices=("asc","desc"),default="desc");x.add_argument("--limit",type=int,default=100);x.add_argument("--page-limit",type=int,default=10)
 x=s.add_parser("prs");x.add_argument("repo");x.add_argument("--state",choices=("open","closed","all"),default="open");x.add_argument("--limit",type=int,default=100);x.add_argument("--page-limit",type=int,default=10);x.add_argument("--text",action="store_true")
 x=s.add_parser("get",help="read-only GET of one REST path with query options; e.g. get repos/O/R/pulls -q state=open --pick number --pick head.ref --text");x.add_argument("path");x.add_argument("-q","--query",action="append",default=[],metavar="KEY=VALUE");x.add_argument("--pick",action="append",default=[],metavar="FIELD");x.add_argument("--text",action="store_true")
 for command in ("read-comment","review","inline-comment"):
  x=s.add_parser(command,help="one comment body: URL, review REPO PR ID, or inline-comment REPO ID; text by default");x.add_argument("targets",nargs="+");x.add_argument("--json",action="store_true")
 x=s.add_parser("pr");x.add_argument("repo");x.add_argument("number",type=int)
 x=s.add_parser("comments");x.add_argument("repo");x.add_argument("number",type=int)
 x=s.add_parser("reviews");x.add_argument("repo");x.add_argument("number",type=int)
 for command in ("inline-comments","review-threads","review-observations"):
  x=s.add_parser(command);x.add_argument("repo");x.add_argument("number",type=int);x.add_argument("--limit",type=int,default=100);x.add_argument("--page-limit",type=int,default=10)
 x=s.add_parser("workflow-runs");x.add_argument("repo");x.add_argument("workflow_id");x.add_argument("--branch");x.add_argument("--head-sha");x.add_argument("--event");x.add_argument("--limit",type=int,default=100);x.add_argument("--page-limit",type=int,default=10);x.add_argument("--page-size",type=int,default=100);x.add_argument("--start-page",type=int,default=1);x.add_argument("--start-offset",type=int,default=0)
 x=s.add_parser("runs");x.add_argument("repo")
 x=s.add_parser("head-runs");x.add_argument("repo");x.add_argument("head_sha");x.add_argument("--event");x.add_argument("--exclude-event",action="append",default=[],help="e.g. dynamic (Copilot code review runs)");x.add_argument("--limit",type=int,default=100);x.add_argument("--page-limit",type=int,default=10)
 x=s.add_parser("artifacts");x.add_argument("repo");x.add_argument("run_id",type=int)
 x=s.add_parser("artifact-file");x.add_argument("repo");x.add_argument("artifact_id",type=int);x.add_argument("member");x.add_argument("--file-bytes",type=int,default=10_000_000)
 x=s.add_parser("checks",help="check-runs of one or more SHAs; --text prints only what needs attention");x.add_argument("repo");x.add_argument("sha",nargs="+");x.add_argument("--min-checks",type=int,default=1);x.add_argument("--text",action="store_true")
 x=s.add_parser("wait-checks",help="poll check-runs on a SHA until green/failed; progress on stderr");x.add_argument("repo");x.add_argument("sha");x.add_argument("--min-checks",type=int,default=1);x.add_argument("--interval",type=float,default=60);x.add_argument("--wait-timeout",type=float,default=900);x.add_argument("--ignore-check",action="append",default=[],metavar="NAME");x.add_argument("--poll-timeout",type=float,default=30,help=argparse.SUPPRESS)
 x=s.add_parser("observe-pr");x.add_argument("repo");x.add_argument("number",type=int);x.add_argument("--min-checks",type=int,default=1)
 x=s.add_parser("pr-status",help="compact status of PRs/Issues: OWNER/REPO N [N...] or OWNER/REPO#N ...");x.add_argument("targets",nargs="+",metavar="OWNER/REPO|N|OWNER/REPO#N");x.add_argument("--last",type=int,default=3);x.add_argument("--exclude-event",action="append",default=None,metavar="EVENT");x.add_argument("--text",action="store_true",help="human-readable lines instead of JSON")
 x=s.add_parser("workflow");x.add_argument("repo");x.add_argument("workflow_id")
 x=s.add_parser("run");x.add_argument("repo");x.add_argument("run_id",type=int);x.add_argument("--attempt",type=int)
 x=s.add_parser("jobs");x.add_argument("repo");x.add_argument("run_id",type=int);x.add_argument("--attempt",type=int)
 x=s.add_parser("job-log");x.add_argument("repo");x.add_argument("run_id",type=int);x.add_argument("job_id",type=int);x.add_argument("--attempt",type=int,required=True);x.add_argument("--log-bytes",type=int,default=1_000_000);x.add_argument("--redact-env",action="append",default=[])
 x=s.add_parser("step-url");x.add_argument("repo");x.add_argument("run_id",type=int);x.add_argument("job_id",type=int);x.add_argument("step_number",type=int);x.add_argument("--line",type=int)
 x=s.add_parser("variable-get");x.add_argument("repo");x.add_argument("name")
 x=s.add_parser("variable-set");x.add_argument("repo");x.add_argument("name");x.add_argument("value");x.add_argument("--write",action="store_true")
 x=s.add_parser("labels",help="add/remove existing labels on one Issue/PR (planned unless --write)");x.add_argument("repo");x.add_argument("number",type=int);x.add_argument("--add",action="append",default=[],metavar="LABEL");x.add_argument("--remove",action="append",default=[],metavar="LABEL");x.add_argument("--write",action="store_true")
 x=s.add_parser("comment");x.add_argument("repo");x.add_argument("number",type=int);x.add_argument("body");x.add_argument("--operation-key");x.add_argument("--sanitize-mentions",action="store_true");x.add_argument("--write",action="store_true")
 try:ns=a.parse_args(argv)
 except SystemExit as e:return int(e.code)
 try:
  if ns.cmd=="capabilities":o=capabilities()
  elif ns.cmd=="git-status":o=git_status(ns.root,max_bytes=ns.output_bytes)
  elif ns.cmd=="git-files":o=git_ls_files(ns.root,max_bytes=ns.output_bytes)
  elif ns.cmd=="git-diff":o=git_diff(ns.root,staged=ns.staged,base=ns.base,head=ns.head,path=ns.path,max_bytes=ns.output_bytes)
  elif ns.cmd=="git-log":o=git_log(ns.root,max_count=ns.count,max_bytes=ns.output_bytes)
  elif ns.cmd=="git-numstat":o=git_log_numstat(ns.root,max_count=ns.count,max_bytes=ns.output_bytes)
  elif ns.cmd=="git-show":o=git_show(ns.root,ns.revision,path=ns.path,max_bytes=ns.output_bytes)
  elif ns.cmd=="git-blame":o=git_blame(ns.root,path=ns.path,max_bytes=ns.output_bytes)
  elif ns.cmd=="git-grep":o=git_grep(ns.root,ns.pattern,max_bytes=ns.output_bytes)
  elif ns.cmd=="git-ignore":o=git_check_ignore(ns.root,ns.paths,max_bytes=ns.output_bytes)
  elif ns.cmd=="git-switch":
   if ns.create and ns.branch is not None:raise ValueError("use either BRANCH or -c NEW_BRANCH")
   if ns.create:o=git_switch(ns.root,ns.create,create=True,write=ns.write)
   elif ns.branch=="-":o=git_switch(ns.root,previous=True,write=ns.write)
   elif ns.branch:o=git_switch(ns.root,ns.branch,write=ns.write)
   else:raise ValueError("branch required")
  elif ns.cmd=="git-work":o=git_work(ns.root,ns.source,ns.branch,upstream=ns.upstream,remote=ns.remote,depth=ns.depth,fetch_timeout=ns.fetch_timeout,write=ns.write)
  elif ns.cmd=="local-position":o=local_position(ns.root)
  elif ns.cmd=="subtree-sync-status":o=subtree_sync_status(ns.root,ns.prefix,ns.other_root,revision=ns.revision,other_ref=ns.other_ref,other_only_paths=ns.other_only_path,timeout=ns.timeout,mainline=ns.mainline)
  elif ns.cmd=="sync-provenance":
   if ns.verify:o=verify_sync_provenance(ns.root,ns.verify,monorepo_root=ns.monorepo_root,timeout=ns.timeout)
   else:
    if not ns.prefix or not ns.other_root:raise ValueError("--prefix and --other-root required")
    o=sync_provenance(ns.root,ns.prefix,ns.other_root,revision=ns.revision,other_ref=ns.other_ref,direction=ns.direction,squash=ns.squash,generated_at=ns.generated_at,other_only_paths=ns.other_only_path,timeout=ns.timeout)
    if ns.output_dir:write_sync_provenance(o,ns.output_dir)
  elif ns.cmd=="split-preflight":o=split_preflight(ns.root,ns.prefix,ns.revision,timeout=ns.timeout)
  elif ns.cmd=="duplicate-files":
   if ns.min_repos<2:raise ValueError("--min-repos must be at least 2")
   o=duplicate_files(ns.owner,[x.strip() for x in ns.repos.split(",") if x.strip()] if ns.repos else None,ignore=() if ns.no_default_ignore else DUPLICATE_IGNORE,extra_ignore=ns.ignore,min_repos=ns.min_repos,include_forks=ns.include_forks,heads=load_child_heads(ns.heads) if ns.heads else None,transport=transport)
  elif ns.cmd=="repo-identity":o=repo_identity(ns.repository,transport=transport)
  elif ns.cmd=="owner-repositories":o=list_owner_repositories(ns.owner,kind=ns.kind,include_forks=ns.include_forks,include_archived=ns.include_archived,transport=transport)
  elif ns.cmd=="pr-identity":o=pr_identity(ns.repository,ns.number,transport=transport)
  elif ns.cmd=="commit-pulls":o=commit_pulls(ns.repository,ns.sha,transport=transport)
  elif ns.cmd=="first-parent":o=first_parent_check(ns.repository,ns.commit,branch=ns.branch,exact=ns.exact,transport=transport,timeout=ns.git_timeout if ns.exact else 30,remote_base=ns.remote_base)
  elif ns.cmd=="pin-audit":o=pin_audit(ns.root,json_globs=tuple(ns.json_globs) if ns.json_globs else PIN_JSON_GLOBS,only_owners=ns.only,remote_base=ns.remote_base,timeout=ns.git_timeout)
  elif ns.cmd=="vendor-lock-check":o=vendor_lock_check(ns.root,ns.lock,ns.rules,ns.parent_ref)
  elif ns.cmd=="vendor-lock-verify-remote":o=vendor_lock_verify_remote(ns.root,ns.lock,upstream=ns.upstream,first_parent=ns.first_parent,check_parent_ref=ns.check_parent_ref,parent_ref=ns.parent_ref,transport=transport)
  elif ns.cmd=="vendor-plan":o=vendor_plan(ns.root,ns.candidate,ns.rules,transport=transport)
  elif ns.cmd=="vendor-matrix":o=vendor_matrix(ns.owner,[x.strip() for x in ns.repos.split(",") if x.strip()] if ns.repos else None,lock_paths=tuple(ns.lock_path) if ns.lock_path else ("vendor.lock.json","tool/python/vendor.lock.json"),heads=load_child_heads(ns.heads) if ns.heads else None,transport=transport)
  elif ns.cmd=="enrollment-progress":o=enrollment_progress(ns.owner,provider=ns.provider,source=ns.source,repos=[x.strip() for x in ns.repos.split(",") if x.strip()] if ns.repos else None,heads=load_child_heads(ns.heads) if ns.heads else None,include_forks=ns.include_forks,transport=transport)
  elif ns.cmd=="subtree-split":o=git_subtree_split(ns.root,ns.prefix,ns.revision,timeout=ns.timeout)
  elif ns.cmd=="repo-metadata":
   if not ns.repository:raise ValueError("--repository or GITHUB_REPOSITORY required")
   if ns.format=="v1":
    o=repository_metadata_v1(ns.root,repository=ns.repository,expected_sha=ns.expected_sha,generated_at=ns.generated_at,
                             tooling=tool_versions([t for t in ns.tool if t!="python"],include_python="python" in ns.tool))
   else:
    if ns.tool:raise ValueError("--tool applies to --format v1 only")
    o=repository_metadata(ns.root,repository=ns.repository,source_sha=ns.source_sha,expected_sha=ns.expected_sha,generated_at=ns.generated_at)
   if ns.output_dir:write_repository_metadata(o,ns.output_dir)
  elif ns.cmd=="overview":o=overview()
  elif ns.cmd=="repo":o=repository(ns.repo,transport=transport)
  elif ns.cmd=="tree":o=tree(ns.repo,ns.ref,depth=None if ns.recursive else ns.depth,exclude_dirs=ns.exclude_dir,exclude_files=ns.exclude_file,transport=transport)
  elif ns.cmd=="repos":o=repositories(ns.owner,transport=transport)
  elif ns.cmd=="repo-inventory":o=repository_inventory(ns.owner,fields=ns.fields,sort=ns.sort,order=ns.order,max_repos=ns.max_repos,max_pages=ns.page_limit,transport=transport)
  elif ns.cmd=="issue":o=issue(ns.repo,ns.number,transport=transport)
  elif ns.cmd=="html-content":o=_html_content_file(ns.file,ns.selector,ns.source_kind,ns.input_bytes,ns.include_controls)
  elif ns.cmd=="content":
   identifier=ns.identifier if ns.kind=="commit" else int(ns.identifier)
   o=content(ns.repo,ns.kind,identifier,transport=transport)
   if ns.field is not None:o=select_content(o,ns.field)
  elif ns.cmd=="issues":o=issues(ns.repo,state=ns.state,max_items=ns.limit,max_pages=ns.page_limit,transport=transport)
  elif ns.cmd=="prs":o=pull_requests(ns.repo,state=ns.state,max_items=ns.limit,max_pages=ns.page_limit,transport=transport)
  elif ns.cmd=="search":o=search(ns.query,kind=ns.kind,sort=ns.sort,order=ns.order,max_items=ns.limit,max_pages=ns.page_limit,transport=transport)
  elif ns.cmd=="pr":o=pr(ns.repo,ns.number,transport=transport)
  elif ns.cmd=="comments":o=comments(ns.repo,ns.number,transport=transport)
  elif ns.cmd=="reviews":o=reviews(ns.repo,ns.number,transport=transport)
  elif ns.cmd in ("inline-comments","review-threads","review-observations"):
   o={"inline-comments":inline_comments,"review-threads":review_threads,"review-observations":review_observations}[ns.cmd](ns.repo,ns.number,max_items=ns.limit,max_pages=ns.page_limit,transport=transport)
  elif ns.cmd=="workflow-runs":o=workflow_run_discovery(ns.repo,ns.workflow_id,branch=ns.branch,head_sha=ns.head_sha,event=ns.event,max_items=ns.limit,max_pages=ns.page_limit,page_size=ns.page_size,start_page=ns.start_page,start_offset=ns.start_offset,transport=transport)
  elif ns.cmd=="runs":o=runs(ns.repo,transport=transport)
  elif ns.cmd=="head-runs":
   if not re.fullmatch(r"[0-9a-fA-F]{40}",ns.head_sha):raise ValueError("invalid head_sha")
   o=run_history(ns.repo,max_items=ns.limit,max_pages=ns.page_limit,head_sha=ns.head_sha,event=ns.event,transport=transport,exclude_events=ns.exclude_event)
  elif ns.cmd=="artifacts":o=artifacts(ns.repo,ns.run_id,transport=transport)
  elif ns.cmd=="artifact-file":o=artifact_file(ns.repo,ns.artifact_id,ns.member,max_bytes=ns.file_bytes,transport=transport)
  elif ns.cmd=="get":o=get(ns.path,ns.query,ns.pick,transport)
  elif ns.cmd in ("read-comment","review","inline-comment"):o=read_comment(ns.targets,{"review":"review","inline-comment":"inline"}.get(ns.cmd),transport)
  elif ns.cmd=="checks":o=checks_for_shas(ns.repo,ns.sha,ns.min_checks,transport)
  elif ns.cmd=="wait-checks":o=wait_checks(ns.repo,ns.sha,min_checks=ns.min_checks,interval=ns.interval,wait_timeout=ns.wait_timeout,ignore=ns.ignore_check,transport=transport,timeout=ns.poll_timeout,progress=lambda line:print(line,file=sys.stderr,flush=True))
  elif ns.cmd=="observe-pr":o=observe_pr(ns.repo,ns.number,ns.min_checks,transport)
  elif ns.cmd=="pr-status":o=status_many(ns.targets,last=ns.last,exclude_events=tuple(ns.exclude_event) if ns.exclude_event is not None else ("dynamic",),transport=transport)
  elif ns.cmd=="workflow":o=workflow(ns.repo,ns.workflow_id,transport=transport)
  elif ns.cmd=="run":o=run(ns.repo,ns.run_id,attempt=ns.attempt,transport=transport)
  elif ns.cmd=="jobs":o=jobs(ns.repo,ns.run_id,attempt=ns.attempt,transport=transport)
  elif ns.cmd=="job-log":o=job_log(ns.repo,ns.run_id,ns.job_id,attempt=ns.attempt,max_bytes=ns.log_bytes,redact=_log_redact_env(ns.redact_env),transport=transport,timeout=_BUDGET.get().remaining())
  elif ns.cmd=="step-url":o={"schema":"gh-identity-step-url/1","url":step_url(ns.repo,ns.run_id,ns.job_id,ns.step_number,ns.line)}
  elif ns.cmd=="variable-get":o=variable(ns.repo,ns.name,transport=transport)
  elif ns.cmd=="variable-set":o=set_variable(ns.repo,ns.name,ns.value,ns.write,transport)
  elif ns.cmd=="labels":o=set_labels(ns.repo,ns.number,ns.add,ns.remove,ns.write,transport)
  else:
   marker=f"<!-- gh-identity:{ns.operation_key} -->" if ns.operation_key else None
   o=post_comment(ns.repo,ns.number,ns.body,ns.write,marker,transport,sanitize_mentions=ns.sanitize_mentions)
 except (ValueError,Error,OSError,GitInspectionError) as e:
  detail={"detail":str(e)} if isinstance(e,PinInputError) else {}
  print(json.dumps({"status":"error","error":getattr(e,"code","git_inspection_failed" if isinstance(e,GitInspectionError) else "invalid_argument"),**detail}),file=sys.stderr);return 2
 if ns.cmd=="git-status" and not ns.json:
  text=render_git_status(o)
  if text:print(text)
 elif ns.cmd in ("read-comment","review","inline-comment") and not ns.json:print(o["body"])
 elif ns.cmd=="pr-status" and ns.text:print(render_status(o))
 elif ns.cmd=="checks" and ns.text:print(render_checks(o))
 elif ns.cmd in ("prs","issues") and ns.text:print(render_list(o))
 elif ns.cmd=="get" and ns.text:print(render_get(o))
 elif ns.cmd in ("vendor-lock-check","vendor-lock-verify-remote") and ns.markdown:print(render_vendor_markdown(o))
 elif ns.cmd=="pin-audit" and ns.text:print(render_pin_audit(o))
 elif ns.cmd=="duplicate-files" and ns.markdown:print(render_duplicates_markdown(o,top=ns.top))
 else:print(json.dumps(o,ensure_ascii=False))
 if ns.cmd=="pin-audit":return 0 if o["ok"] else 1
 if ns.cmd in ("vendor-lock-check","vendor-lock-verify-remote") and ns.ci_check:
  drift=any(r.get("upstream_changed") for r in o["files"])
  return 0 if o["ok"] and not drift else 1
 if ns.cmd in ("job-log","inline-comments","review-threads","review-observations"):return 0 if o["complete"] else 1
 # Gate-style exit codes: 0 only when everything observed is complete and green.
 if ns.cmd in ("checks","head-runs","wait-checks"):return 0 if o["state"]=="green" else 1
 if ns.cmd in ("artifacts","artifact-file"):return 0 if o["complete"] else 1
 if ns.cmd=="observe-pr":return 0 if o["checks"]["state"]=="green" and not o["stale"] else 1
 if ns.cmd in ("comment","variable-set","git-switch","git-work","labels"):
  return 0 if o.get("status") in ("planned","already_exists","already_satisfied","verified") else 1
 return 0
def main(argv=None):
 args=list(sys.argv[1:] if argv is None else argv);limits={}
 try:
  for flag,key,convert in (("--max-pages","max_pages",int),("--max-items","max_items",int),
                           ("--max-bytes","max_bytes",int),("--timeout","timeout",float)):
   if flag in args:
    index=args.index(flag)
    if index+1>=len(args):raise ValueError("missing limit")
    limits[key]=convert(args[index+1]);del args[index:index+2]
  # wait-checks spans many bounded polls; --timeout then bounds each poll.
  if args[:1]==["wait-checks"]:
   if set(limits)-{"timeout"}:raise ValueError("wait-checks accepts --timeout only (per poll)")
   return _main(args+(["--poll-timeout",str(limits["timeout"])] if "timeout" in limits else []))
  with operation(**limits):return _main(args)
 except (ValueError,Error,GitInspectionError) as e:
  print(json.dumps({"status":"error","error":getattr(e,"code","git_inspection_failed" if isinstance(e,GitInspectionError) else "invalid_argument")}),file=sys.stderr)
  return 2

# One outer deadline/budget is shared across nested calls and gh fallback.
for _name in ("_gh","_url","request","pages","repository","repositories","repository_inventory","pr","comments","reviews","inline_comments","review_threads","review_observations",
              "issue","content","content_from_hit","issues","search","pull_requests","run_history","workflow_run_discovery","runs","variable","set_variable","post_comment",
              "resolve_ref","tree","source_identity","get","checks_for_sha","observe_pr","workflow","run","jobs","job_log","artifacts","artifact_file",
              "pages_bounded","item_status","vendor_lock_verify_remote","vendor_matrix","owner_repositories","verify_parent_ref",
              "enrollment_progress","repo_identity","list_owner_repositories","pr_identity","commit_pulls","first_parent_check"):
 globals()[_name]=_bounded(globals()[_name])
if __name__=="__main__":
 raise SystemExit(_http_worker() if sys.argv[1:]==["--_http-worker"] else
                  _job_log_worker() if sys.argv[1:]==["--_job-log-worker"] else main())
