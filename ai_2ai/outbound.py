"""Connexions sortantes limitées à Internet public, contrôlées à la connexion.

Vérifier le nom de domaine avant la requête ne suffit pas : la bibliothèque
HTTP le résout une seconde fois, et un DNS piégé peut alors renvoyer une adresse
interne (DNS rebinding, ex. 169.254.169.254). Ici, l'adresse contrôlée est celle
de la socket réellement ouverte, avant l'envoi du moindre octet.
"""
import ipaddress
import socket

import anyio
import httpcore
import httpx
import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool


BLOCKED_MESSAGE = "Adresse non autorisée (réseau interne ou réservé)."


class BlockedAddressError(OSError):
    pass


def is_public_ip(value):
    try:
        ip = ipaddress.ip_address(str(value).split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _checked(sock):
    peer = sock.getpeername()[0]
    if not is_public_ip(peer):
        sock.close()
        raise BlockedAddressError(BLOCKED_MESSAGE)
    return sock


class _PublicHTTPConnection(HTTPConnection):
    def _new_conn(self):
        return _checked(super()._new_conn())


class _PublicHTTPSConnection(HTTPSConnection):
    def _new_conn(self):
        return _checked(super()._new_conn())


class _PublicHTTPPool(HTTPConnectionPool):
    ConnectionCls = _PublicHTTPConnection


class _PublicHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _PublicHTTPSConnection


class _PublicAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": _PublicHTTPPool, "https": _PublicHTTPSPool}


def public_session():
    """Session `requests` qui refuse toute connexion vers une adresse non publique."""
    session = requests.Session()
    # Un proxy d'environnement serait lui-même une adresse interne : on l'ignore.
    session.trust_env = False
    adapter = _PublicAdapter()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class _PublicNetworkBackend(httpcore.AsyncNetworkBackend):
    """Résout le nom une seule fois, refuse les adresses internes et s'y connecte."""

    def __init__(self):
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as error:
            raise httpcore.ConnectError(str(error)) from error
        addresses = [info[4][0] for info in infos]
        if not addresses or not all(is_public_ip(address) for address in addresses):
            raise httpcore.ConnectError(BLOCKED_MESSAGE)
        # Le nom d'origine reste utilisé pour TLS (SNI et certificat).
        return await self._inner.connect_tcp(
            addresses[0], port, timeout=timeout, local_address=local_address, socket_options=socket_options,
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError(BLOCKED_MESSAGE)

    async def sleep(self, seconds):
        await self._inner.sleep(seconds)


def public_async_transport():
    """Transport `httpx` asynchrone limité aux adresses publiques."""
    transport = httpx.AsyncHTTPTransport()
    transport._pool._network_backend = _PublicNetworkBackend()
    return transport
