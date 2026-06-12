"""Online play client.

Model: deterministic lockstep. The host picks settings + seed; every client
runs the identical simulation. Only the active player's InputFrames travel
over the wire (tick-tagged), relayed by server/relay.py. Bots are computed
locally by every client from the shared RNG, so they cost zero bandwidth.

If a player drops, the host streams empty inputs for their turns so the
match never deadlocks; if they rejoin (same name), the host hands them a
full snapshot and their team back.
"""
import json
import socket
import threading
import queue

from .game import InputFrame

PROTOCOL = 8       # bump when the sim or input encoding changes


class Session:
    def __init__(self, host, port, name, timeout=6.0):
        self.name = name
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(None)
        self.rx = queue.Queue()
        self.pid = None
        self.code = None
        self.host_pid = None
        self.roster = []
        self.alive = True
        self.input_buf = {}
        self.peer_hashes = {}
        self.own_hashes = {}
        self.desynced = False
        self.started = False
        self.snapshot_requests = []     # pids waiting for a snapshot (host)
        self.pending_snapshot = None    # received snapshot (joiner)
        self._buf = b""
        self._lock = threading.Lock()
        t = threading.Thread(target=self._reader, daemon=True)
        t.start()

    # ------------------------------------------------------------ low level
    def _reader(self):
        try:
            while self.alive:
                data = self.sock.recv(65536)
                if not data:
                    break
                self._buf += data
                while b"\n" in self._buf:
                    line, self._buf = self._buf.split(b"\n", 1)
                    if line.strip():
                        try:
                            self.rx.put(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except OSError:
            pass
        self.alive = False
        self.rx.put({"t": "closed"})

    def send(self, msg):
        try:
            with self._lock:
                self.sock.sendall((json.dumps(msg) + "\n").encode())
        except OSError:
            self.alive = False

    def close(self):
        self.alive = False
        try:
            self.sock.close()
        except OSError:
            pass

    def _wait_for(self, types, timeout=6.0):
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                m = self.rx.get(timeout=0.2)
            except queue.Empty:
                continue
            if m["t"] in types:
                return m
            self._handle_common(m)
        raise TimeoutError("no reply from server")

    # --------------------------------------------------------------- lobby
    def create_room(self):
        self.send({"t": "create", "name": self.name, "proto": PROTOCOL})
        m = self._wait_for(("created", "error"))
        if m["t"] == "error":
            raise RuntimeError(m.get("msg", "server error"))
        self.pid, self.code = m["pid"], m["code"]
        self.host_pid = self.pid

    def join_room(self, code):
        self.send({"t": "join", "code": code, "name": self.name,
                   "proto": PROTOCOL})
        m = self._wait_for(("joined", "error"))
        if m["t"] == "error":
            raise RuntimeError(m.get("msg", "room not found"))
        self.pid, self.code = m["pid"], m["code"]
        self.started = m.get("started", False)

    def is_host(self):
        return self.pid == self.host_pid

    def _handle_common(self, m):
        t = m.get("t")
        if t == "roster":
            self.roster = m["players"]
            self.host_pid = m["host"]
        elif t == "input":
            self.input_buf[m["tick"]] = m["d"]
        elif t == "snapshot_request":
            self.snapshot_requests.append(m.get("from"))
        elif t == "snapshot":
            self.pending_snapshot = m
        elif t == "statehash":
            self.peer_hashes[m["tick"]] = m["h"]
        return t

    def poll(self):
        """Drain messages; auto-handle common ones, return the rest."""
        out = []
        while True:
            try:
                m = self.rx.get_nowait()
            except queue.Empty:
                break
            t = self._handle_common(m)
            if t in ("start", "error", "closed", "chat", "rebind"):
                out.append(m)
        return out

    # ---------------------------------------------------------------- game
    def send_input(self, tick, inp: InputFrame):
        self.send({"t": "input", "tick": tick, "d": inp.encode()})

    def check_state(self, tick, h):
        """Record our hash, compare against peers, broadcast ours."""
        self.own_hashes[tick] = h
        if len(self.own_hashes) > 8:
            del self.own_hashes[min(self.own_hashes)]
        self.send({"t": "statehash", "tick": tick, "h": h})
        peer = self.peer_hashes.get(tick)
        if peer is not None and peer != h:
            self.desynced = True
        for k in [k for k in self.peer_hashes if k < tick - 3000]:
            del self.peer_hashes[k]

    def get_input(self, tick):
        d = self.input_buf.pop(tick, None)
        if d is None:
            return None
        return InputFrame.decode(d)

    def drop_old_inputs(self, tick):
        for k in [k for k in self.input_buf if k < tick]:
            del self.input_buf[k]

    def pump(self, game_screen):
        """Process net traffic during a match."""
        for m in self.poll():
            t = m["t"]
            if t == "closed":
                game_screen.net_lost = True
            elif t == "rebind":
                # a player reconnected: their team follows the new pid
                g = game_screen.game
                team = m["team"]
                if 0 <= team < len(g.teams):
                    g.teams[team].control = f"net:{m['new']}"
        # host duties: answer snapshot requests once the game is quiescent
        if self.is_host() and self.snapshot_requests:
            g = game_screen.game
            if g.is_quiescent():
                snap = g.serialize()
                present = {p["pid"] for p in self.roster}
                names = {p["pid"]: p["name"] for p in self.roster}
                for pid in self.snapshot_requests:
                    # hand a reconnecting player their orphaned team back
                    for ti, team in enumerate(g.teams):
                        if team.control.startswith("net:"):
                            old = int(team.control.split(":")[1])
                            if old not in present and \
                                    team.name == names.get(pid, "?"):
                                team.control = f"net:{pid}"
                                self.send({"t": "rebind", "team": ti,
                                           "new": pid})
                                break
                    self.send({"t": "snapshot", "to": pid,
                               "settings": game_screen.settings,
                               "snap": snap,
                               "controls": [t.control for t in g.teams]})
                self.snapshot_requests.clear()

    def request_snapshot(self):
        self.send({"t": "snapshot_request", "to_host": True})

    def present_pids(self):
        return {p["pid"] for p in self.roster}
