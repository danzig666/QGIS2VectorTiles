"""
SSH / SFTP server as a hosting destination: the site goes into one folder
on the server, not versioned (no ``releases/<id>/``, no ``current.json``,
no rollback; see ``folder_publish.py`` for the protocol).

* Transport: the system OpenSSH client. ``sftp`` (which starts ``ssh``)
  runs one batch of commands per session (``sftp -b``), never one process
  per file. No Python SSH library is needed (QGIS ships none). On Windows
  the client is also looked up in ``%SystemRoot%\\System32\\OpenSSH``.
* Authentication: a key file, ssh-agent or the default keys (BatchMode,
  nothing is asked), or a password, which is also a key's passphrase. ssh
  gets the password from a small askpass helper that reads it from the
  sftp process's environment: never on a command line, in a file or in a
  log; the helper's temporary folder is removed afterwards.
* Host keys: a new server's key is accepted on first use
  (``StrictHostKeyChecking=accept-new``); a changed key stops the
  connection with an explanation.
* Paths go into the batch file quoted for sftp's own argument parser:
  spaces, quotes, backslashes, glob characters and non-ASCII names work.
"""

import os
import queue
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from ..errors import Cancelled, PublishingError
from .base import ProviderError

STATE_NAME = ".q2vt-files.json"     # what QWebMap uploaded into the folder (see folder_publish)
TMP_SUFFIX = ".q2vt-tmp"            # upload name until the rename over the target
PROBE_NAMES = (".q2vt-probe-1", ".q2vt-probe-2")
SECRET_ENV = "QWEBMAP_SSH_SECRET"   # the password, in the sftp process's environment only
MAX_LINE = 900                      # sftp parses at most 1023 bytes per batch line
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_ECHO = b"sftp> "

MISSING_CLIENT = (
    "The OpenSSH client (sftp, ssh) was not found on this computer. Windows: Settings → System → "
    "Optional features → add \"OpenSSH Client\"; Linux: install the openssh-client package; "
    "macOS has it built in. Then restart QGIS.")


def find_client(name: str) -> Optional[str]:
    """Path of an OpenSSH program (``sftp``, ``ssh``), or None."""
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":  # the optional Windows feature; QGIS's PATH may lack its folder
        root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
        for system in ("System32", "Sysnative"):  # Sysnative: a 32-bit process on 64-bit Windows
            path = os.path.join(root, system, "OpenSSH", f"{name}.exe")
            if os.path.isfile(path):
                return path
    return None


def sftp_quote(text: str) -> str:
    """One argument of an sftp batch line: in double quotes with ``\\`` and
    ``"`` escaped. Inside quotes sftp escapes the glob characters ``*?[``
    itself, so they stay literal for every command (``put``'s local glob and
    ``rm``'s remote one included)."""
    if not text or _CONTROL.search(text):
        raise PublishingError("Q2VT_PUB_PATH_UNSAFE", f"Not usable in an SFTP command: {text!r}")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def batch_line(command: str, *paths: str, ignore_errors: bool = False) -> str:
    """``command "path" ...``; ``ignore_errors``: a failure does not stop the
    batch (sftp's ``-`` prefix)."""
    line = ("-" if ignore_errors else "") + " ".join([command] + [sftp_quote(p) for p in paths])
    if len(line.encode("utf-8")) > MAX_LINE:
        raise PublishingError("Q2VT_PUB_PATH_UNSAFE", "A path is too long for SFTP (over "
                              f"{MAX_LINE} bytes): {line[:120]}…")
    return line


def local_path(path: str) -> str:
    """A local path for sftp: absolute, with ``/`` (Windows accepts them)."""
    return os.path.abspath(path).replace(os.sep, "/")


def tmp_name(relative: str) -> str:
    """The upload name of ``a/b.json``: ``a/.b.json.q2vt-tmp`` (hidden, same folder)."""
    folder, _, name = relative.rpartition("/")
    return (folder + "/" if folder else "") + f".{name}{TMP_SUFFIX}"


def ancestors(folder: str) -> List[str]:
    """``/srv/www/map`` → ``/srv``, ``/srv/www``, ``/srv/www/map``; ``.`` → none."""
    absolute = folder.startswith("/")
    parts = [p for p in folder.split("/") if p not in ("", ".")]
    return [("/" if absolute else "") + "/".join(parts[:index]) for index in range(1, len(parts) + 1)]


def parse_target(text: str) -> Dict[str, object]:
    """Settings from a pasted ``user@host:/folder``, ``host:folder`` or
    ``sftp://user@host:2222/folder`` (``{}`` for a plain host name)."""
    value = (text or "").strip()
    if re.match(r"^(sftp|ssh|scp)://", value, re.I):
        parsed = urlparse(value)
        if not parsed.hostname:
            return {}
        found: Dict[str, object] = {"host": parsed.hostname}
        if parsed.username:
            found["user"] = unquote(parsed.username)
        try:
            if parsed.port:
                found["port"] = parsed.port
        except ValueError:
            pass
        path = unquote(parsed.path or "")
        if path in ("/~", "/~/"):
            path = ""
        elif path.startswith("/~/"):  # sftp URIs: relative to the home folder
            path = path[3:]
        if path and path != "/":
            found["remote_dir"] = path
        return found
    match = re.match(r"^(?:([^@\s]+)@)?(\[[0-9A-Fa-f:.%]+\]|[^:/\s\[\]@]+)(?::(.*))?$", value)
    if not match or ("@" not in value and ":" not in value):
        return {}
    user, host, path = match.groups()
    found = {"host": host.strip("[]")}
    if user:
        found["user"] = user
    if path and path.strip():
        found["remote_dir"] = path.strip()
    return found


def write_askpass(folder: str) -> str:
    """A helper that prints the password from the environment (ssh runs it
    when it needs a password or a key's passphrase). It holds no secret."""
    if os.name == "nt":
        path = os.path.join(folder, "q2vt-askpass.cmd")
        # Delayed expansion prints the value as is (no re-parsing of & | < > ^).
        text = f"@echo off\r\nsetlocal EnableDelayedExpansion\r\necho(!{SECRET_ENV}!\r\n"
    else:
        path = os.path.join(folder, "q2vt-askpass.sh")
        text = f"#!/bin/sh\nprintf '%s\\n' \"${SECRET_ENV}\"\n"
    with open(path, "w", encoding="ascii", newline="") as handle:
        handle.write(text)
    os.chmod(path, 0o700)
    return path


def askpass_env(password: str, helper: str, base: Optional[dict] = None) -> dict:
    """The sftp process's environment: with a password, ssh asks the helper
    (``SSH_ASKPASS_REQUIRE=force``: also without a display, never the terminal)."""
    env = dict(os.environ if base is None else base)
    env.pop(SECRET_ENV, None)
    if not password:
        return env
    env.update({"SSH_ASKPASS": helper, "SSH_ASKPASS_REQUIRE": "force", SECRET_ENV: password})
    env.setdefault("DISPLAY", ":0")  # OpenSSH before 8.4 uses askpass only with a display
    return env


class SshProvider:
    """One folder on an SSH server, written with the system's sftp."""

    kind = "ssh"

    def __init__(self, destination, credentials=None, known_hosts: Optional[str] = None,
                 config_file: Optional[str] = None, connect_timeout: int = 20):
        from ..profile import normalize_remote_dir, ssh_problems  # pylint: disable=import-outside-toplevel
        problems = ssh_problems(destination)
        if problems:
            raise PublishingError("Q2VT_PUB_DESTINATION", "; ".join(problems))
        self.destination = destination
        self.host = destination.host.strip("[]")
        self.port = int(destination.port or 22)
        self.user = destination.user or (credentials.access_key_id if credentials else "")
        self.password = credentials.secret_access_key if credentials else ""
        self.remote_dir = normalize_remote_dir(destination.remote_dir)
        self.identity_file = os.path.expanduser(destination.identity_file) if destination.identity_file else ""
        self.known_hosts = known_hosts      # tests: a private known_hosts file
        self.config_file = config_file      # tests: os.devnull (the user's ~/.ssh/config is used otherwise)
        self.connect_timeout = connect_timeout
        if self.identity_file and not os.path.isfile(self.identity_file):
            raise PublishingError("Q2VT_PUB_DESTINATION", f"The private key file {self.identity_file} "
                                  "does not exist.")

    def secrets(self) -> List[str]:
        return [self.password] if self.password else []

    def describe(self) -> str:
        """``user@host:folder`` (with the port when it is not 22), for messages."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        text = f"{self.user}@{host}" if self.user else host
        return f"{text}:{self.remote_dir}" + (f" (port {self.port})" if self.port != 22 else "")

    def _redact(self, text: str) -> str:
        for secret in self.secrets():
            text = text.replace(secret, "***")
        return text

    # -- one sftp session ---------------------------------------------------------------
    def command(self, batch: str) -> List[str]:
        """The sftp command line of one batch file (no secret in it)."""
        sftp = find_client("sftp")
        if not sftp:
            raise PublishingError("Q2VT_PUB_DEPENDENCY", MISSING_CLIENT)
        # Before -b: "sftp -b" sets BatchMode=yes itself, and ssh keeps the first value given.
        args = [sftp, "-o", f"BatchMode={'no' if self.password else 'yes'}"]
        if self.password:
            args += ["-o", "NumberOfPasswordPrompts=1"]
        sibling = os.path.join(os.path.dirname(sftp), "ssh" + os.path.splitext(sftp)[1])
        ssh = sibling if os.path.isfile(sibling) else find_client("ssh")  # the same installation's ssh
        if ssh:
            args += ["-S", ssh]
        if self.config_file:
            args += ["-F", self.config_file]
        args += ["-o", "StrictHostKeyChecking=accept-new", "-o", f"ConnectTimeout={self.connect_timeout}",
                 "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4"]
        if self.known_hosts:
            args += ["-o", f'UserKnownHostsFile="{self.known_hosts}"']
        if self.identity_file:
            args += ["-i", self.identity_file, "-o", "IdentitiesOnly=yes"]
        if self.user:
            args += ["-o", f"User={self.user}"]
        host = f"[{self.host}]" if ":" in self.host else self.host
        return args + ["-P", str(self.port), "-b", batch, "--", host]

    def run(self, lines: List[str], progress=None, weights: Optional[List[int]] = None,
            labels: Optional[Dict[int, str]] = None) -> Tuple[str, str]:
        """Run ``lines`` in one sftp session; returns (stdout, stderr).

        sftp echoes each batch line as it starts it, so a line is done when
        the next one is echoed: ``weights`` (e.g. bytes per ``put``) drive
        ``progress`` and ``labels`` (line index → text) are reported when
        their line starts. Cancelling stops sftp at once."""
        weights = weights or [0] * len(lines)
        total = sum(weights) or 1
        work = tempfile.mkdtemp(prefix="q2vt-sftp-")
        try:
            batch = os.path.join(work, "batch.txt")
            with open(batch, "w", encoding="utf-8", newline="\n") as handle:
                handle.write("\n".join(lines) + "\n")
            helper = write_askpass(work) if self.password else ""
            kwargs = {"start_new_session": True} if os.name != "nt" else {
                "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
            try:
                process = subprocess.Popen(  # pylint: disable=consider-using-with
                    self.command(batch), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, env=askpass_env(self.password, helper), **kwargs)
            except OSError as error:
                raise PublishingError("Q2VT_PUB_DEPENDENCY", f"{MISSING_CLIENT} ({error})") from error
            lines_out: "queue.Queue" = queue.Queue()
            errors: List[bytes] = []
            out: List[bytes] = []

            def read_out():
                for raw in process.stdout:
                    lines_out.put(raw)
                lines_out.put(None)

            def read_err():
                errors.append(process.stderr.read())

            readers = [threading.Thread(target=read_out, daemon=True),
                       threading.Thread(target=read_err, daemon=True)]
            for reader in readers:
                reader.start()
            started = done = 0
            while True:
                try:
                    raw = lines_out.get(timeout=0.2)
                except queue.Empty:
                    raw = b""
                if progress is not None and progress.canceled():
                    self._stop(process)
                    raise Cancelled()
                if raw is None:
                    break
                if not raw:
                    continue
                out.append(raw)
                if raw.startswith(_ECHO):
                    done += weights[started - 1] if 0 < started <= len(weights) else 0
                    started += 1
                    if progress is not None:
                        label = (labels or {}).get(started - 1)
                        progress.update(done / total, label or "", force=bool(label))
            code = process.wait()
            for reader in readers:
                reader.join(timeout=5)
            stdout = b"".join(out).decode("utf-8", "replace")
            stderr = self._redact(b"".join(errors).decode("utf-8", "replace").replace("\r\n", "\n"))
            if code != 0:
                failed = lines[started - 1] if 0 < started <= len(lines) else ""
                raise self.classify(stderr, code, failed)
            if progress is not None:
                progress.update(1.0, "", force=True)
            return stdout, stderr
        finally:
            shutil.rmtree(work, ignore_errors=True)  # batch file and askpass helper

    @staticmethod
    def _stop(process) -> None:
        """Stop sftp and the ssh it started (their own process group)."""
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait(timeout=10)

    def classify(self, stderr: str, code: int, failed: str = "") -> ProviderError:
        """A user-facing error from sftp's output (``failed``: the batch line it stopped at)."""
        lower = stderr.lower()
        last = next((line.strip() for line in reversed(stderr.splitlines()) if line.strip()), "")
        detail = f"exit {code}; {failed}\n{stderr.strip()}"[-4000:]
        host = f"[{self.host}]:{self.port}" if self.port != 22 else self.host
        if "remote host identification has changed" in lower or "host key verification failed" in lower:
            return ProviderError(
                "Q2VT_PUB_DESTINATION",
                f"The host key of {self.host} has changed since the last connection: the server was "
                "reinstalled, or someone may be intercepting the connection. Nothing was uploaded. If "
                f"the change is expected (ask the server's administrator), remove the old key with "
                f"\"ssh-keygen -R {host}\" and try again.", detail=detail)
        if re.search(r"permission denied \(|permission denied, please try again|too many authentication "
                     r"failures|no supported authentication methods", lower):
            who = f"user \"{self.user}\"" if self.user else "your default user name"
            hint = "the password or passphrase" if self.password else \
                "the key file, or enter a password (also a key's passphrase)"
            return ProviderError("Q2VT_PUB_CREDENTIALS", f"The server {self.host} refused the login "
                                 f"({who}): check the user name and {hint}.", detail=detail)
        if re.search(r"could not resolve hostname|name or service not known|nodename nor servname", lower):
            return ProviderError("Q2VT_PUB_DESTINATION", f"The server name {self.host} is not known "
                                 "(typo, or no network / DNS).", detail=detail)
        if re.search(r"connection refused|connection timed out|operation timed out|no route to host|"
                     r"network is unreachable|connection reset|kex_exchange_identification", lower):
            return ProviderError("Q2VT_PUB_DESTINATION", f"The server {self.host} cannot be reached on "
                                 f"port {self.port} ({last}). Check the address, the port and the "
                                 "network or firewall.", retryable=True, detail=detail)
        if code == 255 or "connection closed" in lower or "broken pipe" in lower:
            return ProviderError("Q2VT_PUB_UPLOAD", f"The SSH connection to {self.host} failed or was "
                                 f"lost ({last or 'no message'}). Publish again to complete the folder.",
                                 retryable=True, detail=detail)
        if failed.startswith("cd "):
            denied = " (permission denied)" if "permission denied" in lower else ""
            return ProviderError("Q2VT_PUB_DESTINATION", f"The folder {self.remote_dir} does not exist on "
                                 f"the server and could not be created{denied}: {last}", detail=detail)
        if "permission denied" in lower:
            return ProviderError("Q2VT_PUB_DESTINATION", f"Permission denied: {self.user or 'the user'} "
                                 f"cannot write into {self.remote_dir} on {self.host} ({last}).",
                                 detail=detail)
        return ProviderError("Q2VT_PUB_UPLOAD", f"The SFTP transfer stopped: {last or failed}",
                             detail=detail)

    # -- operations ----------------------------------------------------------------------
    def prepare(self, local_dir: str, read_state: bool = True, progress=None) -> Tuple[Optional[bytes], bool]:
        """One session: create the folder if needed, read the state file, and
        write a test file twice to see whether a rename replaces a file
        (OpenSSH's posix-rename: atomic). Returns (state bytes or None,
        atomic replace?)."""
        probe = os.path.join(local_dir, "probe.txt")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("QWebMap write test; deleted at once.\n")
        state = os.path.join(local_dir, "remote-state.json")
        if os.path.exists(state):
            os.remove(state)
        lines = [batch_line("mkdir", folder, ignore_errors=True) for folder in ancestors(self.remote_dir)]
        lines += [batch_line("cd", self.remote_dir), batch_line("lcd", local_path(local_dir))]
        if read_state:
            lines.append(batch_line("get", STATE_NAME, "remote-state.json", ignore_errors=True))
        first, second = PROBE_NAMES
        lines += [batch_line("put", "probe.txt", first), batch_line("put", "probe.txt", second),
                  batch_line("rename", first, second, ignore_errors=True),
                  batch_line("rm", first, ignore_errors=True), batch_line("rm", second)]
        _, stderr = self.run(lines, progress)
        atomic = not re.search(r"(remote rename|couldn't rename)[^\n]*" + re.escape(first), stderr,
                               re.IGNORECASE)
        data = None
        if os.path.exists(state):
            with open(state, "rb") as handle:
                data = handle.read()
        return data, atomic

    def inspect(self) -> List[Tuple[str, bool, str]]:
        """Test connection: log in, create the folder if needed, write and
        delete a test file."""
        local = tempfile.mkdtemp(prefix="q2vt-sftp-test-")
        try:
            _, atomic = self.prepare(local, read_state=False)
        except PublishingError as error:
            name = "client" if error.code == "Q2VT_PUB_DEPENDENCY" else \
                "login" if error.code == "Q2VT_PUB_CREDENTIALS" else "connection"
            return [(name, False, error.message)]
        finally:
            shutil.rmtree(local, ignore_errors=True)
        who = f" as {self.user}" if self.user else ""
        return [("connection", True, f"Logged in to {self.host} (port {self.port}){who}."),
                ("folder", True, f"{self.remote_dir}: exists (created if it was missing); a test file "
                                 "was written and deleted."),
                ("replace", atomic, "Files are replaced atomically (rename over the old file)." if atomic else
                 "The server cannot rename over an existing file: each changed file is briefly missing "
                 "while it is replaced (visitors may get an error for a moment).")]
