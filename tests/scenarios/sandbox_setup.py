"""The setup phase of the two-phase sandbox launch (D3).

Run as ``python -I -S -B sandbox_setup.py <spec.json-text> -- <worker argv>``
inside ``unshare --user --map-root-user --net --mount``: it still holds every
capability of that namespace, so it can mount and bring loopback up, and it
then ``exec``s ``unshare --user --map-user=<uid> --map-group=<gid>`` so the
worker runs as the invoker's uid with no capability in a nested user
namespace that shares this mount namespace (whose mounts it cannot undo).

Standard library only. Progress goes to file descriptor 4 (``userns``,
``setup-ok``); the worker adds ``drop-ok``.
"""

import ctypes
import fcntl
import json
import os
import socket
import struct
import sys

MS_RDONLY, MS_NOSUID, MS_NODEV, MS_NOEXEC = 1, 2, 4, 8
MS_REMOUNT, MS_BIND, MS_NOATIME, MS_NODIRATIME = 32, 4096, 1024, 2048
MS_REC, MS_PRIVATE, MS_RELATIME = 16384, 1 << 18, 1 << 21
_FLAG_NAMES = {"nosuid": MS_NOSUID, "nodev": MS_NODEV, "noexec": MS_NOEXEC, "noatime": MS_NOATIME,
               "nodiratime": MS_NODIRATIME, "relatime": MS_RELATIME}

_libc = ctypes.CDLL(None, use_errno=True)


def _mount(source, target, fstype, flags, data=None):
    result = _libc.mount(source.encode() if source else None, target.encode(), fstype.encode() if fstype else None,
                         ctypes.c_ulong(flags), data.encode() if data else None)
    if result != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, f"mount {source or fstype} on {target}: {os.strerror(errno)}")


def _unescape(text):
    return text.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")


def _mounts_under(path):
    """``[(mountpoint, per-mount flags)]`` for ``path`` and every mount below it."""
    prefix = path.rstrip("/") + "/"
    found = []
    with open("/proc/self/mountinfo", encoding="utf-8") as fh:
        for line in fh:
            fields = line.split()
            mountpoint = _unescape(fields[4])
            if mountpoint == path or mountpoint.startswith(prefix):
                flags = 0
                for option in fields[5].split(","):
                    flags |= _FLAG_NAMES.get(option, 0)
                found.append((mountpoint, flags))
    return found


def protect(path, mode):
    if mode == "hide":
        _mount("none", path, "tmpfs", MS_NOSUID | MS_NODEV, "mode=0555,size=4k")
        _mount(None, path, None, MS_REMOUNT | MS_BIND | MS_RDONLY | MS_NOSUID | MS_NODEV)
        return
    _mount(path, path, None, MS_BIND | MS_REC)
    for mountpoint, flags in _mounts_under(path):
        _mount(None, mountpoint, None, MS_REMOUNT | MS_BIND | MS_RDONLY | flags)


def loopback_up():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        request = struct.pack("16sH14x", b"lo", 0)
        flags = struct.unpack("16sH14x", fcntl.ioctl(sock, 0x8913, request))[1]  # SIOCGIFFLAGS
        fcntl.ioctl(sock, 0x8914, struct.pack("16sH14x", b"lo", flags | 1))  # SIOCSIFFLAGS, IFF_UP
    finally:
        sock.close()


def main():
    spec = json.loads(sys.argv[1])
    worker = sys.argv[sys.argv.index("--") + 1:]
    status = spec["status_fd"]
    os.write(status, b"userns\n")
    _mount(None, "/", None, MS_REC | MS_PRIVATE)
    _mount("none", spec["scratch"], "tmpfs", MS_NOSUID | MS_NODEV, "mode=0700,size=" + spec.get("scratch_size", "1g"))
    for entry in spec["protect"]:
        protect(entry["path"], entry["mode"])
    loopback_up()
    os.write(status, b"setup-ok\n")
    for fd in spec["inherit_fds"]:
        os.set_inheritable(fd, True)
    os.chdir(spec.get("cwd") or "/")
    drop = spec["drop"] or [spec["unshare"], "--user", f"--map-user={spec['uid']}", f"--map-group={spec['gid']}", "--"]
    os.execvpe(drop[0], [*drop, *worker], dict(os.environ))


main()
