import re,html,urllib.parse,urllib.request,http.cookiejar,json,sys
URL='http://sise.cjf.gob.mx/consultasvp/default.aspx'
cj=http.cookiejar.CookieJar(); op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def get(u):
    return op.open(urllib.request.Request(u,headers={'User-Agent':'Mozilla/5.0'}),timeout=90).read().decode('utf-8','replace')
def post(d):
    r=urllib.request.Request(URL,data=urllib.parse.urlencode(d).encode(),
      headers={'User-Agent':'Mozilla/5.0','Content-Type':'application/x-www-form-urlencoded','Referer':URL})
    return op.open(r,timeout=120).read().decode('utf-8','replace')
def state(h):
    return {n:html.unescape(re.search(r'id="%s"[^>]*value="([^"]*)"'%n,h).group(1))
            for n in ['__VIEWSTATE','__VIEWSTATEGENERATOR','__EVENTVALIDATION']}
def combo(h,cid):
    m=re.search(r'id="%s_DropDown".*?</div>\s*</div>'%cid,h,re.S)
    if not m:
        m=re.search(r'id="%s_DropDown"(.*?)(?=<div id="cb|</form>)'%cid,h,re.S)
    if not m: return []
    return [html.unescape(re.sub(r'<[^>]+>','',x)).strip() for x in re.findall(r'<li[^>]*>(.*?)</li>',m.group(0),re.S)]
h=get(URL)
circs=combo(h,'cbCircuitos')
print('circuitos:',len(circs),file=sys.stderr)
res=[]
for i,c in enumerate(circs):
    st=state(h)
    d=dict(st); d['__EVENTTARGET']='cbCircuitos'
    d['__EVENTARGUMENT']=json.dumps({"Text":c,"Value":str(i+1),"Index":i},ensure_ascii=False)
    d['cbCircuitos']=c
    d['cbCircuitos_ClientState']=json.dumps({"logEntries":[],"value":str(i+1),"text":c,"enabled":True,"checkedIndices":[],"checkedItemsTextOverflows":False},ensure_ascii=False)
    try: h2=post(d)
    except Exception as e: res.append({'circuito':c,'error':str(e)}); continue
    res.append({'circuito':c,'tipoOrganismo':combo(h2,'cbTipoOrganismo'),'materia':combo(h2,'cbMateriaOrgano'),
                'organos':combo(h2,'cbOrganos'),'tipoExpediente':combo(h2,'cbTipoExpediente')})
    print(c,'->',res[-1].get('tipoOrganismo'),len(res[-1].get('organos',[])),file=sys.stderr)
    h=h2
json.dump(res,open('consultasvp-organos-por-circuito.json','w'),ensure_ascii=False,indent=1)
print('TOTAL ORGANOS:',sum(len(r.get('organos',[])) for r in res))
print('MATERIAS:',sorted({m for r in res for m in r.get('materia',[])}))
print('TIPOS ORGANISMO:',sorted({m for r in res for m in r.get('tipoOrganismo',[])}))
