#!/usr/bin/env python3
"""GRUBSTORM relay server — pure stdlib, run it anywhere:

    python3 server/relay.py [port]

Players create private rooms and share 4-letter codes. The server only
relays messages; all simulation happens on the clients (deterministic
lockstep), so this can run on a potato.
"""
import json
import random
import socket
import string
import sys
import threading

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 31999

LOCK = threading.RLock()
ROOMS = {}        # code -> {"clients": {pid: Client}, "host": pid, "started": bool}
NEXT_PID = [1]


class Client:
    def __init__(self, conn, addr):
        self.conn = conn
        self.addr = addr
        self.pid = None
        self.name = "?"
        self.code = None
        self.wlock = threading.Lock()

    def send(self, msg):
        try:
            with self.wlock:
                self.conn.sendall((json.dumps(msg) + "\n").encode())
        except OSError:
            pass


def room_roster(code):
    room = ROOMS.get(code)
    if not room:
        return None
    return {"t": "roster",
            "players": [{"pid": c.pid, "name": c.name}
                        for c in room["clients"].values()],
            "host": room["host"]}


def broadcast(code, msg, exclude=None):
    room = ROOMS.get(code)
    if not room:
        return
    for c in list(room["clients"].values()):
        if c.pid != exclude:
            c.send(msg)


def new_code():
    while True:
        code = "".join(random.choices(string.ascii_uppercase, k=4))
        if code not in ROOMS:
            return code


def handle_msg(cl: Client, m):
    t = m.get("t")
    with LOCK:
        if t == "create":
            cl.name = str(m.get("name", "?"))[:16]
            cl.pid = NEXT_PID[0]; NEXT_PID[0] += 1
            code = new_code()
            cl.code = code
            ROOMS[code] = {"clients": {cl.pid: cl}, "host": cl.pid,
                           "started": False}
            cl.send({"t": "created", "code": code, "pid": cl.pid})
            cl.send(room_roster(code))
        elif t == "join":
            code = str(m.get("code", "")).upper()
            room = ROOMS.get(code)
            if not room:
                cl.send({"t": "error", "msg": "room not found"})
                return
            if len(room["clients"]) >= 8:
                cl.send({"t": "error", "msg": "room is full"})
                return
            cl.name = str(m.get("name", "?"))[:16]
            cl.pid = NEXT_PID[0]; NEXT_PID[0] += 1
            cl.code = code
            room["clients"][cl.pid] = cl
            cl.send({"t": "joined", "code": code, "pid": cl.pid,
                     "started": room["started"]})
            broadcast(code, room_roster(code))
        elif cl.code:
            room = ROOMS.get(cl.code)
            if not room:
                return
            if t == "start":
                room["started"] = True
                broadcast(cl.code, {**m, "from": cl.pid})
            elif "to" in m:
                target = room["clients"].get(m["to"])
                if target:
                    target.send({**m, "from": cl.pid})
            elif m.get("to_host"):
                host = room["clients"].get(room["host"])
                if host:
                    host.send({**m, "from": cl.pid})
            else:
                broadcast(cl.code, {**m, "from": cl.pid}, exclude=cl.pid)


def drop_client(cl: Client):
    with LOCK:
        room = ROOMS.get(cl.code)
        if not room:
            return
        room["clients"].pop(cl.pid, None)
        if not room["clients"]:
            del ROOMS[cl.code]
            print(f"[room {cl.code}] empty, closed")
            return
        if room["host"] == cl.pid:
            room["host"] = next(iter(room["clients"]))
        broadcast(cl.code, room_roster(cl.code))


def client_thread(conn, addr):
    cl = Client(conn, addr)
    buf = b""
    try:
        while True:
            data = conn.recv(65536)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    m = json.loads(line)
                except json.JSONDecodeError:
                    continue
                handle_msg(cl, m)
    except OSError:
        pass
    finally:
        drop_client(cl)
        try:
            conn.close()
        except OSError:
            pass
        print(f"[-] {cl.name}@{addr} left")


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(32)
    print(f"GRUBSTORM relay listening on :{PORT}")
    while True:
        conn, addr = srv.accept()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        print(f"[+] connection from {addr}")
        threading.Thread(target=client_thread, args=(conn, addr),
                         daemon=True).start()


if __name__ == "__main__":
    main()
