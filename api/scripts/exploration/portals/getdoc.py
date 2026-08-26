import re,html,urllib.parse,urllib.request,http.cookiejar,sys
URL='http://sise.cjf.gob.mx/consultasvp/default.aspx'
cj=http.cookiejar.CookieJar(); op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
h=open('svp-tema-despido.html',encoding='utf-8',errors='replace').read()
st={n:html.unescape(re.search(r'id="%s"[^>]*value="([^"]*)"'%n,h).group(1)) for n in ['__VIEWSTATE','__VIEWSTATEGENERATOR','__EVENTVALIDATION']}
# primer enlace "Ver sentencia"
tgt=re.findall(r"__doPostBack\(&#39;(grdSentencias\$ctl00\$ctl04\$ctl00)&#39;",h)
print('target:',tgt[:1])
d=dict(st); d['__EVENTTARGET']='grdSentencias$ctl00$ctl04$ctl00'; d['__EVENTARGUMENT']=''
d['RadTabStrip1_ClientState']='{"selectedIndexes":["1"],"logEntries":[],"scrollState":{}}'
d['txtTema']='despido'
req=urllib.request.Request(URL,data=urllib.parse.urlencode(d).encode(),
  headers={'User-Agent':'Mozilla/5.0','Content-Type':'application/x-www-form-urlencoded','Referer':URL})
r=op.open(req,timeout=280)
body=r.read()
print('HTTP',r.status,'bytes',len(body),'ctype',r.headers.get('Content-Type'),'cdisp',r.headers.get('Content-Disposition'))
open('svp-doc-resp.bin','wb').write(body)
t=body.decode('utf-8','replace')
print('word1/SVP:',set(html.unescape(x)[:180] for x in re.findall(r'(?:href|src|window.open\()["\']?([^"\'<> ]*(?:word1|SVP/|VerCaptura|\.pdf|\.doc)[^"\'<> ]*)',t))or 'none')
