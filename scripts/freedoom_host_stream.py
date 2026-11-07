"""固定installerへ有界stdinを送り、raw stderrを捨てて実Popenだけを回収する。"""
import os
import selectors
import signal
import subprocess
import time


def transfer(command,chunks,*,timeout=195.0,limit=65536):
    child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,close_fds=True)
    selector=selectors.DefaultSelector();started=time.monotonic();pending=b"";stdout=bytearray();counts=dict(stdin=0,stdout=0,stderr=0)
    local_error=None;timed_out=False;reaped=False;closed=False;iterator=iter(chunks);exhausted=False
    def close_input():
        nonlocal closed
        if closed:return
        closed=True
        try:selector.unregister(child.stdin)
        except (KeyError,ValueError):pass
        try:child.stdin.close()
        except (OSError,ValueError):pass
    try:
        for name in ("stdin","stdout","stderr"):
            pipe=getattr(child,name);os.set_blocking(pipe.fileno(),False)
            selector.register(pipe,selectors.EVENT_WRITE if name=="stdin" else selectors.EVENT_READ,name)
        while True:
            if time.monotonic()-started>=timeout:timed_out=True;local_error=local_error or "TRANSFER_TIMEOUT";close_input();break
            for key,_ in selector.select(.02):
                if key.data=="stdin":
                    if not pending and not exhausted:
                        try:pending=next(iterator)
                        except StopIteration:exhausted=True
                        except (OSError,ValueError,TypeError):local_error="SOURCE_STREAM_FAILED";exhausted=True
                    if exhausted and not pending:close_input();continue
                    try:written=os.write(key.fd,pending)
                    except BlockingIOError:continue
                    except (BrokenPipeError,OSError):close_input();continue # remoteの元拒否をdrainして残す。
                    counts["stdin"]+=written;pending=pending[written:];continue
                try:data=os.read(key.fd,8192)
                except BlockingIOError:continue
                if not data:selector.unregister(key.fd);continue
                counts[key.data]+=len(data)
                if key.data=="stdout" and counts["stdout"]<=limit:stdout.extend(data)
                if counts[key.data]>limit:local_error=local_error or "REMOTE_OUTPUT_LIMIT";close_input()
            if child.poll() is not None and not any(k.data in {"stdout","stderr"} for k in selector.get_map().values()):break
        close_input()
        if child.poll() is None:
            try:child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:os.killpg(child.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:os.killpg(child.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    child.wait(timeout=2)
        else:child.wait(timeout=2)
        reaped=True
    finally:
        close_input()
        if child.poll() is None:
            try:os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                child.wait(timeout=2)
        until=time.monotonic()+1
        while any(k.data in {"stdout","stderr"} for k in selector.get_map().values()) and time.monotonic()<until:
            for key,_ in selector.select(.02):
                if key.data=="stdin":continue
                data=os.read(key.fd,8192)
                if not data:selector.unregister(key.fd);continue
                counts[key.data]+=len(data)
                if key.data=="stdout" and counts["stdout"]<=limit:stdout.extend(data)
                if counts[key.data]>limit:local_error=local_error or "REMOTE_OUTPUT_LIMIT"
        drained=not any(k.data in {"stdout","stderr"} for k in selector.get_map().values())
        selector.close();child.stdout.close();child.stderr.close()
        close=getattr(iterator,"close",None)
        if close is not None:close()
    return dict(stdout=bytes(stdout),returncode=child.returncode,output_bytes=counts,local_error=local_error,timed_out=timed_out,reaped=reaped,pipes_drained=drained)
