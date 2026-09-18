"""Verified TLS connections; refuse a plaintext handshake before sending credentials."""
import ssl

import pymysql
from pymysql.constants import CLIENT


class VerifiedConnection(pymysql.connections.Connection):
    def _request_authentication(self):
        if not self.server_capabilities & CLIENT.SSL:
            raise pymysql.OperationalError(2026, "MySQL server does not support required TLS")
        return super()._request_authentication()


def tls_context(env, prefix="AMO"):
    # Legacy PREFERRED values no longer weaken TLS. Public roots are used unless
    # a private CA is explicitly configured. All connections verify the hostname.
    return ssl.create_default_context(cafile=env.get(prefix + "_DB_SSL_CA") or None)


def connect(**kwargs):
    if not isinstance(kwargs.get("ssl"), ssl.SSLContext):
        raise ValueError("A verified TLS context is required")
    context = kwargs["ssl"]
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise ValueError("Certificate and hostname verification are required")
    return VerifiedConnection(**kwargs)
