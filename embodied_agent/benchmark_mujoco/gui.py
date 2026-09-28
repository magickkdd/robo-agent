"""A browser view of one running episode (the GUI half of this package).

Two pieces, deliberately split by *files* rather than by threads:

* `GuiSink` runs inside the episode and writes `latest_<view>.png` plus `state.json`
  into a directory. It never blocks on a client and a client dying never reaches the
  simulator;
* `serve` is a plain static HTTP server with one generated page that polls those two
  files. The image is the benchmark's own camera; the right-hand column is the agent's
  page — plan rows, obligations, the last decision it published and the event tail.

Why a web page anyway. `DISPLAY` is *unset* in this environment (unset, not empty - and glfw
agrees: `X11: The DISPLAY environment variable is missing`), so a `render_mode="human"` window
would not appear as launched. That is **not** because the host has no X display: with
`DISPLAY=:0`, `glfw.init()` returns success and enumerates a primary monitor (WSLg), so such a
window is one environment variable away - whether MuJoCo's own viewer actually maps on it is
untested and this module does not claim either way. The browser view is chosen for a different
reason: this package renders offscreen (`env.py` builds the env with `render_mode="rgb_array"`;
there is no `/dev/dri` render node, so software GL is the only path), and publishing *that*
buffer plus the agent's own page over localhost puts the picture, the plan/obligation rows, the
last published decision and the event tail in one place - no window manager to drive, and no
second renderer to keep in sync with the one the VLM channel reads. A native window would show a
*third* viewpoint (the simulator's own camera, at the simulator's own pace), not more information:
the agent never sees it, and nothing about a decision can be read off it.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

INDEX_HTML = """<!doctype html><meta charset="utf-8">
<title>MetaWorld x v0.2 decision loop</title>
<style>
 body{background:#111;color:#ddd;font:13px/1.5 ui-monospace,Menlo,Consolas,monospace;margin:0}
 #wrap{display:flex;gap:14px;padding:14px;align-items:flex-start}
 #left img{width:480px;max-width:48vw;background:#000;border:1px solid #333}
 #right{flex:1;min-width:420px}
 h3{margin:14px 0 4px;color:#7fd;font-weight:600;letter-spacing:.04em}
 pre{white-space:pre-wrap;word-break:break-word;background:#181818;border:1px solid #2a2a2a;
     padding:8px;margin:0;max-height:38vh;overflow:auto}
 .k{color:#f9d66b}.ok{color:#8fe388}.bad{color:#ff7b72}.dim{color:#888}
 table{border-collapse:collapse}td{padding:1px 10px 1px 0;vertical-align:top}
</style>
<div id=wrap><div id=left><img id=frame alt=frame><div class=dim id=cap></div></div>
<div id=right><h3>episode</h3><pre id=head>…</pre><h3>plan / obligations</h3><pre id=plan></pre>
<h3>decision stream</h3><pre id=dec></pre><h3>events</h3><pre id=ev></pre></div></div>
<script>
let n=0;
async function tick(){
  n++;
  try{
    const s=await (await fetch('state.json?n='+n)).json();
    document.getElementById('frame').src='latest_'+(s.view||'corner2')+'.png?n='+n;
    document.getElementById('head').textContent=
      (s.env_name||'?')+'  layout '+(s.task_index??'-')+'\\n'+
      'step '+(s.env_steps??'-')+'/'+(s.max_env_steps??'-')+
      '   round '+(s.round??'-')+'   skill_calls '+(s.skill_calls??'-')+
      '\\nlast action: '+(s.action||'-')+'  '+(s.skill||'')+
      (s.args?('  '+JSON.stringify(s.args)):'')+
      '\\nheld: '+(s.held??'-')+'   progress: '+JSON.stringify(s.progress||{})+
      '\\nofficial (post-hoc, never shown to the agent): '+(s.official_success??'not yet'));
    document.getElementById('cap').textContent=
      'camera '+(s.view||'-')+'  written '+(s.written_at||'');
    document.getElementById('plan').textContent=JSON.stringify(s.plan||{},null,1);
    document.getElementById('dec').textContent=(s.decisions||[]).join('\\n');
    document.getElementById('ev').textContent=(s.events||[]).join('\\n');
  }catch(e){document.getElementById('head').textContent='waiting for the run… '+e}
  setTimeout(tick,700);
}
tick();
</script>
"""


class GuiSink:
    """Writes the live picture. Called from inside the backend's step loop.

    `live` is an optional zero-argument callable the runner installs once the policy and
    the runtime exist: it returns the agent-side columns (decisions so far, plan rows,
    event tail). It is called every publish so the page moves while the episode runs;
    without it the viewer only fills in when the episode is already over."""

    def __init__(self, out_dir: str, *, every_steps: int = 10, view: str = "corner2",
                 live: Optional[Any] = None):
        self.out_dir = out_dir
        self.every = max(1, int(every_steps))
        self.view = view
        self.live = live
        self.frames_written = 0
        self._last_at = 0.0
        os.makedirs(out_dir, exist_ok=True)

    def publish(self, backend: Any, extra: Optional[dict] = None) -> None:
        frame = backend.capture(self.view)
        if frame is not None:
            try:
                import imageio.v2 as iio
                iio.imwrite(os.path.join(self.out_dir, f"latest_{self.view}.png"), frame)
                self.frames_written += 1
            except Exception:  # noqa: BLE001 - a viewer must never break a run
                pass
        state = {"env_name": backend.env_name, "task_index": backend.task_index,
                 "env_steps": int(backend.steps), "max_env_steps": int(backend.max_env_actions),
                 "view": self.view, "written_at": time.strftime("%H:%M:%S"),
                 "frames_published": self.frames_written,
                 "measured": backend.measured()["sites"], "hand": backend.measured()["hand"]}
        path = os.path.join(self.out_dir, "state.json")
        try:
            if self.live is not None:
                state.update(self.live() or {})
            state.update(extra or {})
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=1)
        except Exception:  # noqa: BLE001
            pass

    def on_step(self, backend: Any) -> None:
        if backend.steps % self.every == 0:
            self.publish(backend)


def serve(out_dir: str, port: int = 8093) -> int:
    """Serve one run directory until interrupted.

    Bound to the loopback address on purpose: this is a viewer for the machine running
    the benchmark, and a run directory holds event tails and absolute paths."""
    import http.server
    import functools

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path in ("/", "/index.html"):
                body = INDEX_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return super().do_GET()

        def log_message(self, *a):  # silence the request spam
            return

    os.makedirs(out_dir, exist_ok=True)
    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", int(port)),
        functools.partial(Handler, directory=os.path.abspath(out_dir)))
    print(f"GUI on http://127.0.0.1:{int(port)}/  (serving {out_dir})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


__all__ = ["GuiSink", "serve", "INDEX_HTML"]
