#!/usr/bin/env python3
"""One bench, one run at a time: a lock kept on the bench Pi  ->  BENCH LOCK ...

Two runs against the one bench unit at the same time spoil each other: each
restarts the unit under the other, each saves "as found" what the other has
staged, and each restore puts the other's test state back (2026-11-23: a
profile stage and a Home Assistant backfill bench, started by two sessions
five minutes apart). The unit, the simulator and the Pi are one bench, so
the lock is one directory on the Pi, made with `mkdir` (atomic): whoever
makes it holds the bench.

  take     make the lock for --owner. Prints
           TAKEN when it is yours now, OWN when that owner holds it already
           (a run under a lock taken by hand: it neither takes nor releases),
           HELD with the holder, what it runs and since when otherwise.
           A lock is stale, and taken over, when its holder's process on
           this PC is gone, or, for a holder without a process to look at
           (a lock taken by hand, another PC), when it is older than its
           --ttl-min. --break takes it over whatever it is.
  release  remove the lock when --owner holds it (RELEASED, NOT YOURS, NONE)
  status   who holds the bench (FREE, or the holder)
  selftest the rules above against the Pi, on a lock directory of its own
           -> BENCH LOCK SELFTEST PASS / FAIL

usage: bench_lock.py take --owner NAME --what "<stage or task>" [--pid N]
                          [--ttl-min 120] [--break] [--host rpi001]
       bench_lock.py release --owner NAME [--host rpi001]
       bench_lock.py status | selftest [--host rpi001]
exit:  0 TAKEN / RELEASED / FREE / PASS, 2 OWN, 3 HELD / NOT YOURS / FAIL,
       4 the Pi was not reached (no lock can be told: the caller decides)

`test.ps1` takes it for every stage that touches the bench (not for `list`,
`host` and `check`) and releases it at its end; its own process is the one a
later run looks for. Work by hand (a bench script through a tunnel of your
own, a firmware upload) takes it by hand, from PowerShell (the Bash tool's
ssh does not know the Pi's host key):

  python tools\\testbench\\lib\\bench_lock.py take --owner <who> --what "<task>"
  $env:WICAN_BENCH_OWNER = '<who>'    # test.ps1 then runs under that lock
  python tools\\testbench\\lib\\bench_lock.py release --owner <who>
"""
import base64
import json
import os
import platform
import subprocess
import sys
import time

LOCK_DIR = ".wican_bench_lock"
EXIT_OK, EXIT_OWN, EXIT_HELD, EXIT_UNREACHED = 0, 2, 3, 4


class Unreached(Exception):
    """the Pi did not answer over ssh"""


def ssh(host, script):
    """one command on the Pi; its output lines"""
    try:
        p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, script],
                           capture_output=True, text=True, timeout=40)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Unreached(str(exc))
    if p.returncode != 0:
        raise Unreached((p.stderr or p.stdout or "ssh exit %d" % p.returncode).strip()[:200])
    return p.stdout.splitlines()


def pid_alive(pid):
    """a process of THIS PC: True, False, or None when it cannot be told"""
    if not pid or pid <= 0:
        return None
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH", "/FO", "CSV"],
                                 capture_output=True, text=True, timeout=20).stdout
            return ('"%d"' % pid) in out
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (OSError, subprocess.TimeoutExpired):
        return None


def read(host, lock_dir):
    """the lock as it stands: (holder dict, its age in seconds, its stamp), or (None, 0, "") when the bench is free"""
    for _ in range(4):
        lines = ssh(host, 'd="$HOME/%s"; if [ -d "$d" ]; then echo HELD; cat "$d/owner.json" 2>/dev/null; echo; '
                          'cat "$d/since" 2>/dev/null; echo; date +%%s; else echo FREE; fi' % lock_dir)
        if not lines or lines[0] != "HELD":
            return None, 0, ""
        try:
            holder = json.loads(lines[1])
            return holder, max(0, int(lines[-1]) - int(lines[2])), lines[2].strip()
        except (ValueError, IndexError):
            time.sleep(1.0)     # the directory is there, its files are being written
    return {"owner": "?", "what": "a lock without an owner file", "pc": "?", "pid": 0, "ttl_min": 0}, 10 ** 9, ""


def remove(host, lock_dir, stamp):
    """The lock taken away, but only the one that was read (its stamp): a lock another run made in
    the meantime stays. The look and the removal are one step on the Pi (flock)."""
    ssh(host, 'd="$HOME/%s"; ( flock 9; [ "$(cat "$d/since" 2>/dev/null)" = "%s" ] && rm -rf "$d" ) 9>"$HOME/%s.flk"; echo done'
        % (lock_dir, stamp, lock_dir))


def stale(holder, age_s):
    """why a lock may be taken over ('' = it may not)"""
    if holder.get("pc") == platform.node() and int(holder.get("pid") or 0) > 0:
        alive = pid_alive(int(holder["pid"]))
        if alive is False:
            return "its process (pid %s on this PC) is gone" % holder["pid"]
        if alive is True:
            return ""
    ttl = int(holder.get("ttl_min") or 0)
    if age_s > ttl * 60:
        return "it is %d min old, over its %d min" % (age_s // 60, ttl)
    return ""


def describe(holder, age_s):
    since = time.strftime("%H:%M", time.localtime(time.time() - age_s))
    who = "pid %s on %s" % (holder.get("pid"), holder.get("pc")) if holder.get("pid") else "taken by hand on %s" % holder.get("pc")
    return "%s: %s, since %s (%d min), %s" % (holder.get("owner"), holder.get("what"), since, age_s // 60, who)


def take(host, owner, what, pid, ttl_min, force, lock_dir=LOCK_DIR):
    mine = {"owner": owner, "what": what, "pc": platform.node(), "pid": pid, "ttl_min": ttl_min}
    blob = base64.b64encode(json.dumps(mine).encode()).decode()
    make = ('d="$HOME/%s"; if mkdir "$d" 2>/dev/null; then echo %s | base64 -d > "$d/owner.json"; date +%%s > "$d/since"; '
            'echo TAKEN; else echo HELD; fi' % (lock_dir, blob))
    for _ in range(3):
        if ssh(host, make)[:1] == ["TAKEN"]:
            print("TAKEN by %s for %s" % (owner, what))
            return EXIT_OK
        holder, age, stamp = read(host, lock_dir)
        if holder is None:
            continue                # released between the two looks: once more
        if holder.get("owner") == owner and not force:
            print("OWN: " + describe(holder, age))
            return EXIT_OWN
        why = "--break" if force else stale(holder, age)
        if not why:
            print("HELD by " + describe(holder, age))
            return EXIT_HELD
        print("STALE (%s), taken over from %s" % (why, describe(holder, age)))
        remove(host, lock_dir, stamp)
    print("HELD: the lock changed hands three times while it was asked for")
    return EXIT_HELD


def release(host, owner, lock_dir=LOCK_DIR):
    holder, age, stamp = read(host, lock_dir)
    if holder is None:
        print("NONE: the bench was not locked")
        return EXIT_OK
    if holder.get("owner") != owner:
        print("NOT YOURS, held by " + describe(holder, age))
        return EXIT_HELD
    remove(host, lock_dir, stamp)
    print("RELEASED by %s (held %d min)" % (owner, age // 60))
    return EXIT_OK


def status(host, lock_dir=LOCK_DIR):
    holder, age, _ = read(host, lock_dir)
    if holder is None:
        print("FREE")
        return EXIT_OK
    why = stale(holder, age)
    print("HELD by " + describe(holder, age) + ((" [stale: %s]" % why) if why else ""))
    return EXIT_HELD


def selftest(host):
    """the rules, on a directory of its own (a real run's lock is never touched)"""
    d = LOCK_DIR + "_selftest"
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok)))
        print("%s: %s%s" % ("PASS" if ok else "FAIL", name, (" (%s)" % detail) if detail != "" else ""), flush=True)

    def gone_pid():
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        return p.pid

    ssh(host, 'rm -rf "$HOME/%s"' % d)
    check("a free bench reads FREE", status(host, d) == EXIT_OK)
    check("the first owner takes it", take(host, "self-a", "selftest", os.getpid(), 60, False, d) == EXIT_OK)
    check("a second owner is refused while the first one's process lives", take(host, "self-b", "selftest", os.getpid(), 60, False, d) == EXIT_HELD)
    check("the holder asking again is told it is its own", take(host, "self-a", "selftest", os.getpid(), 60, False, d) == EXIT_OWN)
    check("another owner cannot release it", release(host, "self-b", d) == EXIT_HELD)
    check("the holder releases it", release(host, "self-a", d) == EXIT_OK and read(host, d)[0] is None)
    check("a release of a free bench is no error", release(host, "self-a", d) == EXIT_OK)
    take(host, "self-c", "selftest", gone_pid(), 600, False, d)
    check("a lock whose process on this PC is gone is taken over, whatever its age", take(host, "self-d", "selftest", os.getpid(), 60, False, d) == EXIT_OK
          and (read(host, d)[0] or {}).get("owner") == "self-d")
    release(host, "self-d", d)
    take(host, "self-e", "selftest", 0, 600, False, d)
    check("a lock taken by hand (no process) is held inside its time", take(host, "self-f", "selftest", os.getpid(), 60, False, d) == EXIT_HELD)
    check("--break takes it over", take(host, "self-f", "selftest", os.getpid(), 60, True, d) == EXIT_OK)
    release(host, "self-f", d)
    take(host, "self-g", "selftest", 0, 0, False, d)
    time.sleep(2)
    check("a lock taken by hand is stale past its time and taken over", take(host, "self-h", "selftest", os.getpid(), 60, False, d) == EXIT_OK)
    check("the last holder releases it and the bench is free", release(host, "self-h", d) == EXIT_OK and status(host, d) == EXIT_OK)
    ssh(host, 'rm -rf "$HOME/%s" "$HOME/%s.flk"' % (d, d))
    failed = [n for n, ok in checks if not ok]
    print("BENCH LOCK SELFTEST %s (%d checks, %d failed)" % ("PASS" if not failed else "FAIL", len(checks), len(failed)))
    return EXIT_HELD if failed else EXIT_OK


def main():
    args = sys.argv[1:]

    def opt(name, default):
        return args[args.index(name) + 1] if name in args else default

    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    host = opt("--host", "rpi001")
    owner = opt("--owner", os.environ.get("WICAN_BENCH_OWNER") or "")
    if args[0] in ("take", "release") and not owner:
        print("name the owner: --owner <who> (or WICAN_BENCH_OWNER); a release has to name the same one")
        return 2
    try:
        if args[0] == "take":
            return take(host, owner, opt("--what", "a run by hand"), int(opt("--pid", "0")), int(opt("--ttl-min", "120")), "--break" in args)
        if args[0] == "release":
            return release(host, owner)
        if args[0] == "status":
            return status(host)
        if args[0] == "selftest":
            return selftest(host)
    except Unreached as exc:
        print("UNREACHED: the Pi (%s) did not answer over ssh: %s" % (host, exc))
        return EXIT_UNREACHED
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
