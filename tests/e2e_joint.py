#!/usr/bin/env python3
"""Real PDF pipeline with a local reference translator; NOT an LLM quality test.

Run in the engine venv with PYTHONPATH=<checkout>/engine. Everything, including
model requests, stays local. The reference answer is returned ONLY if both source
fragments arrive in one JSON input item.
"""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import textwrap
import fitz

ROOT=Path(__file__).resolve().parents[1]
REFERENCE='以下结论适用于这份合成文档中描述的每一项实验。应将这些观察结果理解为一个连续的论证。在我们的所有实验中，当学习率设为0.0001以下时，模型均会收敛。这个条件针对的是同一个模型，并未引入另一项独立实验。翻译后的完整段落应保留这一关系。'
TAIL='The following conclusion applies to every experiment described in this synthetic document. The observations should be interpreted as a single continuous argument. In all of our experiments, the model converges when the learning rate is'
HEAD='set below one ten-thousandth. This condition concerns the same model and does not introduce a separate experiment. The complete paragraph should retain this relationship in translation.'

def main():
    parser=argparse.ArgumentParser();parser.add_argument('workdir',type=Path);a=parser.parse_args();work=a.workdir.resolve();work.mkdir(parents=True,exist_ok=True)
    from pdf2rea_engine import joint_paragraph,llm_hook
    assert Path(joint_paragraph.__file__).resolve().is_relative_to(ROOT/'engine')
    d=fitz.open();p=d.new_page(width=595,height=842);p.insert_text((60,60),'Synthetic cross-page paragraph test',fontsize=16)
    body='This document contains invented text for testing paragraph continuity. It is not a scientific result or a user document. The paragraphs below test whether a translation engine preserves sentence meaning across a physical page boundary.'
    for text,y in [(body,105),(TAIL,660)]:
        for i,line in enumerate(textwrap.wrap(text,width=77)):p.insert_text((60,y+i*16),line,fontsize=11)
    p=d.new_page(width=595,height=842)
    for i,line in enumerate(textwrap.wrap(HEAD,width=77)):p.insert_text((60,76+i*16),line,fontsize=11)
    p.insert_text((60,750),'End of synthetic test.',fontsize=9);fixture=work/'cross-page.pdf';d.save(fixture);d.close()
    records=[]
    def pseudo(text):
        parts=[];last=0
        for m in joint_paragraph.PROTECTED.finditer(text):
            parts.append(re.sub(r"[A-Za-z][A-Za-z'-]*",lambda x:'词'*max(1,round(len(x.group())/4)),text[last:m.start()]));parts.append(m.group());last=m.end()
        parts.append(re.sub(r"[A-Za-z][A-Za-z'-]*",lambda x:'词'*max(1,round(len(x.group())/4)),text[last:]));return ''.join(parts)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])));prompt=payload['messages'][-1]['content'];items=llm_hook.parse_items(prompt)
            record={'path':self.path,'item_count':len(items),'inputs':items,'reference_used':False};result=[]
            for item in items:
                plain=' '.join(item['input'].split())
                is_joint='the model converges when the learning rate is set below' in plain.lower()
                result.append({'id':item['id'],'output':REFERENCE if is_joint else pseudo(item['input'])});record['reference_used']|=is_joint
            records.append(record);content=json.dumps(result,ensure_ascii=False) if items else pseudo(prompt.split('Now translate the following text:')[-1])
            response={'id':'local-fixture','object':'chat.completion','created':0,'model':'local-reference','choices':[{'index':0,'message':{'role':'assistant','content':content},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
            data=json.dumps(response,ensure_ascii=False).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    options=work/'pdf2rea.json';options.write_text(json.dumps({'enabled':True,'run_id':'joint-local-reference','cross_page':{'mode':'joint','require_open_ending':True},'terminology':{'numeric_policy':'keep','reuse_ledger':False}}))
    env=os.environ.copy();env['PYTHONPATH']=str(ROOT/'engine');env['PDF2REA_CONFIG']=str(options);env['PDF2REA_CONFIG_DIR']=str(work/'config');env['PDF2REA_TRANSLATOR_CACHE']=str(work/'translator-cache');env['PDF2REA_LEDGER_PATH']=str(work/'ledger.json');env['NO_PROXY']='127.0.0.1,localhost';env.pop('PDF2REA_DISABLE',None)
    command=[sys.executable,'-B',str(ROOT/'tests/engine_runner.py'),str(fixture),'--openaicompatible','--openai-compatible-base-url',f'http://127.0.0.1:{server.server_port}/v1','--openai-compatible-api-key','local-fixture-only','--openai-compatible-model','local-reference','--lang-in','en','--lang-out','zh-CN','--no-auto-extract-glossary','--watermark-output-mode','no_watermark','--output',str(work/'output')]
    try:
        with (work/'run.log').open('w') as log:process=subprocess.run(command,env=env,cwd=work,stdout=log,stderr=subprocess.STDOUT,timeout=180)
    finally:server.shutdown();server.server_close()
    (work/'requests.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
    report={'exit':process.returncode,'model_endpoint':'local loopback fixture','reference_is_not_model_quality_evidence':True,'requests':len(records),'joined_reference_requests':sum(r['reference_used'] for r in records)}
    status=json.loads((work/'pdf2rea.status.json').read_text());report['cross_page']=status.get('stats',{}).get('cross_page',{})
    paths=list((work/'output').glob('*.mono.pdf'));report['mono_files']=[str(p) for p in paths]
    if paths:
        d=fitz.open(paths[0]);texts=[p.get_text() for p in d];report['pages']=len(d);report['page_texts']=texts
        compact=lambda s:''.join(s.split());report['complete_reference_in_pdf']=compact(REFERENCE) in compact(''.join(texts))
        for i,p in enumerate(d):p.get_pixmap(matrix=fitz.Matrix(1,1)).save(work/f'page-{i+1}.png')
        d.close()
    (work/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2))
    assert report['exit']==0 and report['joined_reference_requests']==1,report
    assert report['cross_page'].get('joined')==1 and report['cross_page'].get('context_fallbacks')==0,report
    assert report.get('complete_reference_in_pdf'),report

if __name__=='__main__':main()
