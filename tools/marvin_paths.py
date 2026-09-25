"""Pure prevalidation of new evidence destinations; no transport or output writes."""

import errno
import os
from pathlib import Path
import signal
import stat


def _reject_kernel_path(path):
    if len(path.parts) > 1 and path.parts[1] in ("dev", "proc", "sys"):
        raise ValueError("Output must not be a device or kernel-interface path.")


def new_output_path(output, *, allow_missing_parents=False):
    """Return a new absolute destination after snapshot-only path checks.

    Every supplied ancestor must be a directory, never a symlink or special
    file. Modern callers can allow missing parents; legacy callers require
    existing parents. Nothing is created here. Directory and file writers must
    still use exclusive creation: these checks cannot lock ancestors against
    concurrent replacement.
    """
    if output is None:
        raise ValueError("A new output destination is required.")
    if type(allow_missing_parents) is not bool:
        raise ValueError("allow_missing_parents must be an explicit boolean.")
    supplied = Path(output)
    absolute = supplied if supplied.is_absolute() else Path.cwd() / supplied
    path = Path(os.path.abspath(absolute))
    _reject_kernel_path(absolute)
    _reject_kernel_path(path)
    for supplied_parent in reversed(absolute.parents):
        # Check each supplied prefix, including those later removed by "..".
        # Normalizing each prefix also checks reachable ancestors after a
        # missing/.. pair without following any known symlink.
        parent = Path(os.path.abspath(supplied_parent))
        _reject_kernel_path(parent)
        try:
            info = parent.lstat()
        except FileNotFoundError:
            if not allow_missing_parents:
                raise
        else:
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("Output parents must be directories, not symlinks or special files.")
    try:
        path.lstat()
    except FileNotFoundError:
        return path
    raise FileExistsError(errno.EEXIST, "Output already exists; captures are never overwritten or resumed.", str(path))


def create_private_output(path, owned, name):
    """Register an exclusive private file with cleanup before restoring SIGINT."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    old_mask = None
    try:
        if hasattr(signal, "pthread_sigmask"):
            old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
        owned[f"{name}_fd"] = os.open(path, flags, 0o600)
        owned[f"{name}_path"] = Path(path)
    finally:
        if old_mask is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
