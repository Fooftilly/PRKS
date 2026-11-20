"""A throwaway self-signed certificate for the TLS tests (#1339).

``openssl`` writes a P-256 key and a one-day certificate for ``127.0.0.1``
into a temporary directory the caller removes. Nothing here is a real
credential, and nothing is written into the repository.
"""

from __future__ import annotations

import os
import shutil
import ssl
import subprocess
import tempfile


def self_signed(directory: str) -> tuple[str, str]:
    """``(cert_file, key_file)`` in ``directory``: a self-signed pair for 127.0.0.1."""
    openssl = shutil.which("openssl")
    if openssl is None:
        raise RuntimeError("the TLS tests need the openssl command")
    cert = os.path.join(directory, "cert.pem")
    key = os.path.join(directory, "key.pem")
    subprocess.run(
        [
            openssl, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
            "-nodes", "-keyout", key, "-out", cert, "-days", "1",
            "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


def encrypted_key(directory: str, key_file: str) -> str:
    """``key_file`` re-encrypted with a passphrase, beside it."""
    out = os.path.join(directory, "key-encrypted.pem")
    subprocess.run(
        [shutil.which("openssl") or "openssl", "pkey", "-in", key_file, "-out", out,
         "-aes256", "-passout", "pass:not-asked-for"],
        check=True,
        capture_output=True,
    )
    return out


def temporary_pair(test) -> tuple[str, str]:
    """A self-signed pair removed when ``test`` (a ``TestCase``) ends."""
    directory = tempfile.mkdtemp(prefix="prks-tls-")
    test.addCleanup(shutil.rmtree, directory, True)
    return self_signed(directory)


def client_context(cert_file: str) -> ssl.SSLContext:
    """A client context that trusts only ``cert_file`` and checks the host name."""
    return ssl.create_default_context(cafile=cert_file)
