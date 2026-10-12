#!/usr/bin/env python3
"""One ssh session per host for a whole bench run.

A bench that polls a remote host (the Pi, the betty VPS) with a fresh `ssh
host cmd` every few seconds opens hundreds of sessions in an hour; betty
stopped answering TCP 22 from the bench PC after two hours of that on
2026-10-12 (a rate ban: it still pinged). Windows OpenSSH has no
ControlMaster, so this keeps ONE `ssh host bash` process per host and runs
each command through its stdin, reading until a sentinel line carries the
exit code. Commands are single lines of shell (pipes and quotes fine; a
heredoc is not: install files with `put()` instead, which uses base64).

    ch = SshChannel("betty")
    rc, out = ch.run("curl -s -m 5 http://10.9.0.2/api/status")
    ch.put("/tmp/rx.py", open("rx.py").read())
    ch.close()

`run()` returns (rc, text); rc 255 and a note when the session is gone
(the next call opens a new one). A command that hangs is cut at `timeout`
seconds and the session is dropped (the remote command may run on).
"""
import base64
import os
import subprocess
import threading
import time
import uuid


class SshChannel:
    def __init__(self, host, connect_timeout=12):
        self.host = host
        self.connect_timeout = connect_timeout
        self.proc = None
        self.lock = threading.Lock()
        self.sessions = 0

    # ---- the session ---------------------------------------------------------------
    def _open(self):
        self._drop()
        # binary pipes: a text-mode pipe on Windows writes CR LF for every
        # newline and the remote bash then sees `true\r` (command not found)
        self.proc = subprocess.Popen(
            ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={self.connect_timeout}",
             "-o", "ServerAliveInterval=30", "-T", self.host, "bash", "--noprofile",
             "--norc", "-s"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.sessions += 1
        # a probe: the shell must answer before the first real command
        rc, out = self._exchange("true", 20)
        if rc != 0:
            self._drop()
            return False
        return True

    def _drop(self):
        p = self.proc
        self.proc = None
        if p is None:
            return
        try:
            p.stdin.close()
        except OSError:
            pass
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def _exchange(self, cmd, timeout):
        """Write one command, read until its sentinel. Returns (rc, text)."""
        tag = "__SSHCH_" + uuid.uuid4().hex + "__"
        lines = []
        rc = [255]
        done = threading.Event()

        def reader():
            try:
                for raw in self.proc.stdout:
                    ln = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if ln.startswith(tag):
                        try:
                            rc[0] = int(ln[len(tag):].strip() or "255")
                        except ValueError:
                            rc[0] = 255
                        done.set()
                        return
                    lines.append(ln)
            except (ValueError, OSError):
                pass
            done.set()

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        try:
            self.proc.stdin.write(f"{cmd}\necho {tag}$?\n".encode("utf-8"))
            self.proc.stdin.flush()
        except (OSError, ValueError):
            return 255, "ssh session gone (write)"
        if not done.wait(timeout):
            self._drop()
            return 255, "\n".join(lines) + f"\n(ssh channel: {timeout} s timeout, session dropped)"
        if not self.alive() and rc[0] == 255 and not lines:
            return 255, "ssh session gone"
        return rc[0], "\n".join(lines)

    # ---- the API -------------------------------------------------------------------
    def run(self, cmd, timeout=60):
        with self.lock:
            if not self.alive() and not self._open():
                return 255, f"cannot open an ssh session to {self.host}"
            rc, out = self._exchange(cmd, timeout)
            if rc == 255 and out.startswith("ssh session gone"):
                # the session died under us (a write to a closed pipe, or
                # EOF without the sentinel): one retry on a fresh session
                if self._open():
                    rc, out = self._exchange(cmd, timeout)
            return rc, out

    def put(self, path, text, mode="644"):
        """Write a text file on the host (base64 over the channel: no heredoc)."""
        b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
        chunks = [b64[i:i + 4000] for i in range(0, len(b64), 4000)] or [""]
        rc, out = self.run(f": > {path}.b64")
        for c in chunks:
            rc, out = self.run(f"printf '%s' '{c}' >> {path}.b64")
            if rc != 0:
                return rc, out
        return self.run(f"base64 -d {path}.b64 > {path} && rm -f {path}.b64 && chmod {mode} {path}")

    def close(self):
        with self.lock:
            self._drop()


if __name__ == "__main__":
    import sys
    host = sys.argv[1] if len(sys.argv) > 1 else "rpi001"
    ch = SshChannel(host)
    t0 = time.time()
    for i in range(5):
        rc, out = ch.run("echo hello $((1+1)); hostname")
        print(i, rc, out.replace("\n", " | "))
    rc, out = ch.run("exit 3")
    print("exit code pass-through:", rc, "(expected: the session ends, 255 or 3)")
    rc, out = ch.run("echo back; id -un")
    print("after a dropped session:", rc, out.replace("\n", " | "), "sessions", ch.sessions)
    rc, out = ch.put("/tmp/sshch_test.txt", "line one\nline two\n")
    rc2, out2 = ch.run("cat /tmp/sshch_test.txt; rm -f /tmp/sshch_test.txt")
    print("put:", rc, "cat:", rc2, out2.replace("\n", " | "))
    print(f"{time.time() - t0:.1f} s, sessions {ch.sessions}")
    ch.close()
    sys.exit(0 if ch.sessions <= 2 else 1)
