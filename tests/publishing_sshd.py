"""A throwaway OpenSSH server for the SSH / SFTP destination tests.

Own host key, an authorized test key (and one with a passphrase), 127.0.0.1
on a free unprivileged port, ``sshd -D`` in the foreground from a temporary
config; ``stop()`` ends exactly that process. ``available()`` is False when
sshd or the OpenSSH client is missing (the tests are skipped then).
"""

import getpass
import os
import shutil
import socket
import subprocess
import time

SSHD = "/usr/sbin/sshd"
PASSPHRASE = "correct horse \"battery\" $HOME `id` é"


def available() -> bool:
    return os.path.exists(SSHD) and all(shutil.which(name) for name in ("ssh", "sftp", "ssh-keygen"))


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def keygen(path: str, passphrase: str = "") -> None:
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", passphrase, "-C", "q2vt-test", "-f", path],
                   check=True, capture_output=True)


class SshServer:
    def __init__(self, folder: str):
        self.folder = folder
        self.process = None
        self.port = 0
        self.user = getpass.getuser()
        self.host_key = os.path.join(folder, "host_key")
        self.client_key = os.path.join(folder, "client_key")
        self.locked_key = os.path.join(folder, "locked_key")   # needs PASSPHRASE
        self.known_hosts = os.path.join(folder, "known_hosts")
        self.log = os.path.join(folder, "sshd.log")

    def start(self) -> "SshServer":
        os.makedirs(self.folder, exist_ok=True)
        try:
            os.makedirs("/run/sshd", exist_ok=True)  # sshd's privilege separation folder
        except OSError:
            pass
        keygen(self.host_key)
        keygen(self.client_key)
        keygen(self.locked_key, PASSPHRASE)
        authorized = os.path.join(self.folder, "authorized_keys")
        with open(authorized, "w", encoding="utf-8") as handle:
            for key in (self.client_key, self.locked_key):
                with open(key + ".pub", encoding="utf-8") as public:
                    handle.write(public.read())
        self.port = free_port()
        config = os.path.join(self.folder, "sshd_config")
        with open(config, "w", encoding="utf-8") as handle:
            handle.write("\n".join([
                f"Port {self.port}", "ListenAddress 127.0.0.1", f"HostKey {self.host_key}",
                f"PidFile {os.path.join(self.folder, 'sshd.pid')}", f"AuthorizedKeysFile {authorized}",
                "UsePAM no", "StrictModes no", "PermitRootLogin prohibit-password",
                "PasswordAuthentication no", "KbdInteractiveAuthentication no",
                "Subsystem sftp internal-sftp", ""]))
        with open(self.log, "w", encoding="utf-8") as log:
            self.process = subprocess.Popen(  # pylint: disable=consider-using-with
                [SSHD, "-D", "-e", "-f", config], stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        end = time.time() + 15
        while time.time() < end:
            if self.process.poll() is not None:
                break
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return self
            except OSError:
                time.sleep(0.1)
        self.stop()
        with open(self.log, encoding="utf-8") as handle:
            raise RuntimeError("sshd did not start: " + handle.read()[-2000:])

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        self.process = None

    def destination(self, remote_dir: str, **settings):
        from publishing.models import DestinationConfig  # pylint: disable=import-outside-toplevel
        values = dict(kind="ssh", host="127.0.0.1", port=self.port, user=self.user, remote_dir=remote_dir,
                      identity_file=self.client_key)
        values.update(settings)
        return DestinationConfig(**values)

    def provider(self, destination, credentials=None):
        """A provider that ignores the user's own ssh config and known hosts."""
        from publishing.providers.ssh import SshProvider  # pylint: disable=import-outside-toplevel
        return SshProvider(destination, credentials, known_hosts=self.known_hosts, config_file=os.devnull)
