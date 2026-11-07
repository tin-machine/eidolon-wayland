"""私有KWin virtual上で実GTKのwire分類だけを返す。trace/画像は保存しない。"""
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
import threading
import time
from gtk_interaction_run import InteractionTrace
from sdl_probe_run import group_live


class IdentityTrace(InteractionTrace):
    def __init__(self,nonce):
        super().__init__(nonce);self.history={};self.first={}
    def line(self,line):
        super().line(line)
        match=re.search(r'xdg_toplevel[@#](\d+)\.set_app_id\("([^"\\]*)"\)',line)
        if match:
            top=int(match[1]);role="main" if match[2]==self.expected[0] else "dialog" if match[2]==self.expected[1] else "other"
            rows=self.history.setdefault(top,[])
            if not rows or rows[-1]!=role:rows.append(role)
        match=re.search(r'wl_surface[@#](\d+)\.commit\(',line)
        if match and self.attached.get(int(match[1]))=="shm":
            for top,surface in self.top_surfaces.items():
                if surface==int(match[1]) and top not in self.first:self.first[top]=self.roles.get(top,"other")
    def summary(self):
        result=super().summary()
        result["identity"]={"final_toplevels":{r:list(self.roles.values()).count(r) for r in ("main","dialog")},
            "first_shm_has_final_role":bool(self.roles) and set(self.first)==set(self.roles) and all(self.first.get(top)==role for top,role in self.roles.items()),
            "known_dialog_transition":any(self.roles.get(top)=="dialog" and rows==["main","dialog"] for top,rows in self.history.items()),
            "initial_ids_known":bool(self.history) and all(rows[0] in ("main","dialog") for rows in self.history.values())}
        return result


class VirtualWayland:
    def __enter__(self):
        self.temporary=tempfile.TemporaryDirectory(prefix="gtk-private-wayland-");self.root=Path(self.temporary.name)
        for name in ("runtime","home","config","cache","data"):(self.root/name).mkdir(mode=0o700)
        self.environment={k:v for k,v in os.environ.items() if k not in {"DISPLAY","WAYLAND_DISPLAY","DBUS_SESSION_BUS_ADDRESS","SESSION_MANAGER","KDE_FULL_SESSION"}}
        self.environment.update(HOME=str(self.root/"home"),XDG_RUNTIME_DIR=str(self.root/"runtime"),XDG_CONFIG_HOME=str(self.root/"config"),
            XDG_CACHE_HOME=str(self.root/"cache"),XDG_DATA_HOME=str(self.root/"data"),QT_QPA_PLATFORM="offscreen",QT_QPA_PLATFORMTHEME="generic",
            QT_NO_XDG_DESKTOP_PORTAL="1",GTK_USE_PORTAL="0",GIO_USE_VFS="local",GSETTINGS_BACKEND="memory",KWIN_COMPOSE="Q",LIBGL_ALWAYS_SOFTWARE="1")
        # systemのservice directoriesを含めずportal等のdaemon/FUSE自動起動を許さない。
        config=self.root/"session.conf"
        config.write_text('<busconfig><type>session</type><listen>unix:tmpdir='+str(self.root/'runtime')+'</listen><auth>EXTERNAL</auth><policy context="default"><allow send_destination="*"/><allow receive_sender="*"/><allow own="*"/></policy></busconfig>')
        config.chmod(0o600)
        self.server=subprocess.Popen(["dbus-run-session","--config-file="+str(config),"--","kwin_wayland","--virtual","--no-lockscreen","--no-global-shortcuts","--no-kactivities",
            "--socket","gtk-fixed","--width","1024","--height","768"],env=self.environment,stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        self.server_bytes=0;self.stop=threading.Event()
        def discard():
            selector=selectors.DefaultSelector()
            for pipe in (self.server.stdout,self.server.stderr):os.set_blocking(pipe.fileno(),False);selector.register(pipe,selectors.EVENT_READ)
            try:
                while selector.get_map() and not self.stop.is_set():
                    for key,_ in selector.select(.05):
                        data=os.read(key.fd,8192);self.server_bytes+=len(data)
                        if not data:selector.unregister(key.fd)
            finally:selector.close()
        self.reader=threading.Thread(target=discard,daemon=True);self.reader.start()
        deadline=time.monotonic()+6
        while time.monotonic()<deadline and os.waitid(os.P_PID,self.server.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is None:
            if (self.root/"runtime/gtk-fixed").is_socket():return self
            time.sleep(.02)
        self.__exit__(None,None,None);raise RuntimeError("GTK_PRIVATE_WAYLAND_UNAVAILABLE")
    def __exit__(self,*_):
        # wrapperを先にreapするとKWinがcacheを書き終える前にdirectory削除へ進み得る。
        # leaderはWNOWAITで保持し、実spawn group全体の終了後にreapする。
        try:os.killpg(self.server.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        until=time.monotonic()+3
        while group_live(self.server.pid) and time.monotonic()<until:time.sleep(.02)
        if group_live(self.server.pid):
            try:os.killpg(self.server.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            until=time.monotonic()+2
            while group_live(self.server.pid) and time.monotonic()<until:time.sleep(.02)
        self.server_reclaimed=not group_live(self.server.pid)
        self.server.wait(timeout=2)
        self.stop.set();self.reader.join(timeout=2);self.server.stdout.close();self.server.stderr.close()
        if not self.server_reclaimed:raise RuntimeError("GTK_PRIVATE_WAYLAND_GROUP_NOT_RECLAIMED")
        self.temporary.cleanup()
    def run(self,binary,case,nonce):
        env=dict(self.environment,WAYLAND_DISPLAY="gtk-fixed",WAYLAND_DEBUG="client",GDK_BACKEND="wayland",GDK_GL="disable",GDK_RENDERING="image",
            GTK_IM_MODULE="gtk-im-context-simple",GSETTINGS_BACKEND="memory",GTK_USE_PORTAL="0",DBUS_SESSION_BUS_ADDRESS="unix:path="+str(self.root/"no-session-bus"))
        trace=IdentityTrace(nonce);output=bytearray();selector=selectors.DefaultSelector()
        child=subprocess.Popen([str(binary),"--case",case,"--run-id",nonce],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        for name in ("stdout","stderr"):
            pipe=getattr(child,name);os.set_blocking(pipe.fileno(),False);selector.register(pipe,selectors.EVENT_READ,name)
        deadline=time.monotonic()+8;timed_out=False
        try:
            while selector.get_map():
                if time.monotonic()>deadline:timed_out=True;break
                for key,_ in selector.select(.03):
                    data=os.read(key.fd,8192)
                    if not data:selector.unregister(key.fd);continue
                    if key.data=="stderr":trace.feed(data)
                    else:
                        output.extend(data)
                        if len(output)>65536:raise RuntimeError("GTK_FIXTURE_OUTPUT_LIMIT")
            if not timed_out:child.wait(timeout=1)
        finally:
            if child.poll() is None:
                try:os.killpg(child.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:child.wait(timeout=2)
                except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=1)
            selector.close();child.stdout.close();child.stderr.close()
        reports=[json.loads(line) for line in output.splitlines()]
        return dict(client_returncode=child.returncode,timed_out=timed_out,trace=trace.summary(),reports=reports)
