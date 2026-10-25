#!/usr/bin/env python3
"""Start only the marked 知译 PDF runtime; never touch the stock PDF2zh server."""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import time
import urllib.request

OWNER='pdf2rea@qoder.dev'
COMPAT_VERSION='1.1.2'


def read_api(url, endpoint):
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url+endpoint, timeout=2) as response:
        return json.load(response)


def health(root, url):
    try:
        data=read_api(url,'/health')
        if data.get('status')=='ok' and Path(data.get('outputDir','')).resolve()==(root/'translated').resolve():
            return {'ok':True,'status':'running','version':data.get('version'),'outputDir':data['outputDir'],
                    'zhiyiCompatVersion':data.get('zhiyiCompatVersion',''),'pid':data.get('pid')}
        return {'ok':False,'status':'wrong_service','message':'该地址不属于此 知译 PDF 独立环境'}
    except Exception:
        return {'ok':False,'status':'stopped','message':'知译 PDF 独立服务未启动'}


def compatible(result):
    try:
        current=tuple(int(part) for part in str(result.get('zhiyiCompatVersion','')).split('.'))
        expected=tuple(int(part) for part in COMPAT_VERSION.split('.'))
        return len(current)==3 and current>=expected
    except ValueError:
        return False


def active_tasks(url):
    data=read_api(url,'/api/tasks')
    if not isinstance(data,dict) or not isinstance(data.get('tasks'),list):
        raise ValueError('服务没有返回可验证的任务列表')
    tasks=data['tasks']
    if any(not isinstance(task,dict) for task in tasks):
        raise ValueError('服务任务列表格式异常')
    return [task for task in tasks if task.get('active') is not False and task.get('finished') is not True]


def owned_pid(root, port, result):
    """Require a PID file, same uid, exact private script and matching port."""
    pid_file=root/'pdf2rea-service.pid'
    try:
        if pid_file.is_symlink():return None
        pid=int(pid_file.read_text().strip())
        if pid<=1 or pid in {os.getpid(),os.getppid()}:return None
        reported=result.get('pid')
        if reported is not None and int(reported)!=pid:return None
        process=subprocess.run(['/bin/ps','-p',str(pid),'-o','uid=','-o','command='],capture_output=True,text=True,timeout=3)
        if process.returncode:return None
        uid,command=process.stdout.strip().split(None,1)
        if int(uid)!=os.getuid():return None
        arguments=shlex.split(command)
        scripts={str(root/name) for name in ('server.py','run_pdf2rea.py','server_compat.py')}
        index=next((i for i,arg in enumerate(arguments) if arg in scripts),None)
        if index is None or any(arg in {'-c','-m'} for arg in arguments[:index]):return None
        tail=arguments[index+1:]
        matches_port=any(arg==f'--port={port}' or (arg=='--port' and i+1<len(tail) and tail[i+1]==str(port)) for i,arg in enumerate(tail))
        return pid if matches_port else None
    except (OSError,ValueError,StopIteration,subprocess.SubprocessError):
        return None


def pid_exists(pid):
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True


def stop_for_upgrade(root, url, port, result):
    try:
        if active_tasks(url):
            return {'ok':False,'status':'busy','message':'服务正在处理任务；完成后再次启动/重连以更新兼容组件'}
    except Exception:
        return {'ok':False,'status':'requires_restart','message':'无法确认服务任务已结束，未重启；请任务结束后手动重启独立服务'}
    pid=owned_pid(root,port,result)
    if pid is None:
        return {'ok':False,'status':'requires_restart','message':'服务需要更新，但无法验证其进程归属，未停止任何进程；请手动重启知译 PDF 独立服务'}
    # Re-read the PID ownership immediately before signalling. Never kill a
    # process merely because it happens to listen on the configured port.
    if owned_pid(root,port,result)!=pid:
        return {'ok':False,'status':'requires_restart','message':'服务进程信息已变化，未停止任何进程；请手动重启独立服务'}
    try:os.kill(pid,signal.SIGTERM)
    except ProcessLookupError:return None
    except OSError:
        return {'ok':False,'status':'requires_restart','message':'无法正常停止独立服务，请手动重启'}
    deadline=time.monotonic()+12
    while time.monotonic()<deadline:
        if not pid_exists(pid):return None
        time.sleep(.2)
    return {'ok':False,'status':'requires_restart','message':'已请求独立服务正常退出，但仍未结束；未强制停止，请稍后重试'}


def control(root, action):
    root=root.expanduser().absolute()
    for path in (root,root/'pdf2rea-runtime.json',root/'server.py',root/'server_compat.py',root/'pdf2rea-service.lock',root/'pdf2rea-service.log',root/'pdf2rea-service.pid'):
        if path.is_symlink():raise ValueError('独立环境的运行文件不能是符号链接')
    marker=json.loads((root/'pdf2rea-runtime.json').read_text())
    if marker.get('owner')!=OWNER or root.is_symlink():raise ValueError('拒绝操作未标记的独立环境')
    venv=root/'zotero-pdf2zh-next-venv'
    if venv.is_symlink():raise ValueError('独立环境不能是原版环境的链接')
    port=int(marker.get('server_port',8891));url=f'http://127.0.0.1:{port}'
    if not 0<port<65536 or port==8890:raise ValueError('独立服务端口无效，不能使用原版 8890 端口')
    with (root/'pdf2rea-service.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        result=health(root,url)
        if action=='status' or result['status']=='wrong_service':return result
        if result['ok'] and compatible(result):return result
        python=venv/'bin/python';runner=root/'server_compat.py'
        if not python.is_file() or not runner.is_file():raise ValueError('独立环境不完整，请运行项目中的启动入口准备环境')
        upgraded=False
        if result['ok']:
            blocked=stop_for_upgrade(root,url,port,result)
            if blocked:return blocked
            upgraded=True
        with socket.socket() as probe:
            probe.settimeout(.5)
            if probe.connect_ex(('127.0.0.1',port))==0:
                return {'ok':False,'status':'port_busy','message':f'{port} 端口被其他程序占用'}
        env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1';env.pop('PYTHONPATH',None)
        with (root/'pdf2rea-service.log').open('a') as log:
            child=subprocess.Popen([str(python),'-B',str(runner),'--runtime',str(root),'--port',str(port)],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,cwd=root,env=env,start_new_session=True,close_fds=True)
        (root/'pdf2rea-service.pid').write_text(str(child.pid)+'\n')
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            result=health(root,url)
            if result['ok']:
                if not compatible(result):return {'ok':False,'status':'requires_restart','message':'服务未加载最新兼容组件，请检查独立服务启动入口'}
                result['started']=True;result['upgraded']=upgraded;return result
            if child.poll() is not None:
                return {'ok':False,'status':'failed','message':f'服务启动失败，日志：{root / "pdf2rea-service.log"}'}
            time.sleep(.3)
        return {'ok':False,'status':'starting','message':'服务仍在启动，稍后点“启动/重连”重试'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','status']);parser.add_argument('--runtime',required=True,type=Path);args=parser.parse_args()
    try:result=control(args.runtime.expanduser().absolute(),args.action)
    except Exception as e:result={'ok':False,'status':'error','message':str(e)}
    print(json.dumps(result,ensure_ascii=False));return 0 if result['ok'] else 1

if __name__=='__main__':raise SystemExit(main())
