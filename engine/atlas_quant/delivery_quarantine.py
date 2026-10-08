"""Permanent encrypted evidence for explicitly rejected delivery, never replayed."""
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import uuid

MAXIMUM = 24 * 1024 * 1024 + 16384
PRIVATE_KEYS = {'_snapshotKey','_claimRequestId','_bundleKey','_bundleFormat','_datasetKey','_marketKey','_terminalConfirmed'}


def fail():
    from .runner import RunnerError
    raise RunnerError('DELIVERY_QUARANTINE', '拒绝原件隔离记录无法核对；保留原件并停止。')


def location(spool,payload):
    identity={'id':payload['id'],'leaseToken':payload['leaseToken']}
    key=hashlib.sha256((identity['id']+'\0'+identity['leaseToken']).encode()).hexdigest()
    root=spool.root/'quarantine';folder=root/key
    return identity,root,folder,folder/'record.enc',spool.aad+b':quarantine-v1:'+key.encode()


def read(spool,payload):
    identity,root,folder,path,aad=location(spool,payload)
    if not root.exists() and not root.is_symlink():return None
    try:
        for node in (root,folder):
            if node.is_symlink():raise ValueError()
            if node.exists() and (not node.is_dir() or stat.S_IMODE(node.stat().st_mode)&0o077):raise ValueError()
        if not path.exists() and not path.is_symlink():return None
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode)&0o077 or path.stat().st_size>MAXIMUM:raise ValueError()
        raw=path.read_bytes()
        if raw[:4]!=b'AQQ1':raise ValueError()
        value=json.loads(spool.cipher.decrypt(raw[4:16],raw[16:],aad))
        if set(value)!={'schemaVersion','identity','original','rejection'} or type(value['schemaVersion']) is not int or value['schemaVersion']!=1 or value['identity']!=identity:raise ValueError()
        original=value['original']
        if not isinstance(original,dict) or any(original.get(k)!=v for k,v in identity.items()):raise ValueError()
        for key in PRIVATE_KEYS | {'bundleId','result'}:
            if key in payload and key!='_terminalConfirmed' and original.get(key)!=payload[key]:raise ValueError()
        return value
    except Exception:fail()


def preserve(spool,payload,error):
    previous=read(spool,payload)
    if previous is not None:return previous
    identity,root,folder,path,aad=location(spool,payload)
    for node in (root,folder):
        node.mkdir(mode=0o700,exist_ok=True)
        if node.is_symlink() or not node.is_dir() or stat.S_IMODE(node.stat().st_mode)&0o077:fail()
    import re
    remote=getattr(error,'remote_code',None)
    if not isinstance(remote,str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{1,79}',remote):remote=None
    value={'schemaVersion':1,'identity':identity,'original':payload,
           'rejection':{'httpStatus':error.http_status,'clientCode':error.code,'serverCode':remote,'recordedAt':time.time()}}
    content=json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
    if len(content)+32>MAXIMUM:fail()
    nonce=os.urandom(12);raw=b'AQQ1'+nonce+spool.cipher.encrypt(nonce,content,aad)
    temp=folder/(uuid.uuid4().hex+'.tmp')
    try:
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        # Same identity is owned by the already locked runner; never overwrite evidence.
        os.link(temp,path);temp.unlink()
        for node in (folder,root,spool.root):
            fd=os.open(node,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
    except OSError:fail()
    return value


def rejected(payload):
    return {'id':payload['id'],'leaseToken':payload['leaseToken'],
            **{k:payload[k] for k in PRIVATE_KEYS if k in payload},'_quarantined':True,
            'error':{'code':'RESULT_REJECTED','message':'研究结果未通过服务端接收校验；加密原件已隔离保留，未自动重算或重投。'}}
