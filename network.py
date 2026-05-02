"""
network.py -- DHT-based peer discovery for the MoE Network
============================================================
Each node joins a small Kademlia DHT.  Specialist nodes register themselves
under a well-known key.  Any node can read the list to find peers and call
their /query endpoints.

This is the "works across multiple networks" piece — Kademlia gossips peer
info via UDP, so as long as one bootstrap host is reachable from each device
the network forms.

Why DHT (not a centralised registry):
  - No single point of failure
  - No server to host
  - Scales as the network grows
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from dataclasses import dataclass
from typing import Optional

from kademlia.network import Server

PEER_LIST_KEY = "moe:peers"
PEER_TTL_SEC  = 90  # peers are dropped if not refreshed within this window


@dataclass
class Peer:
    specialty: str
    label:     str
    url:       str
    last_seen: float

    @classmethod
    def from_dict(cls, d: dict) -> "Peer":
        return cls(
            specialty=d["specialty"],
            label=d.get("label", d["specialty"].upper()),
            url=d["url"],
            last_seen=float(d.get("last_seen", time.time())),
        )

    def to_dict(self) -> dict:
        return {
            "specialty": self.specialty,
            "label":     self.label,
            "url":       self.url,
            "last_seen": self.last_seen,
        }


def _local_ip() -> str:
    """Best-effort local LAN IP (so peers reach us, not 127.0.0.1)."""
    # Try the UDP socket trick first (fastest)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    # Fallback: enumerate network interfaces
    try:
        import psutil
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    ip = addr.address
                    if ip and not ip.startswith(("127.", "169.254")):
                        return ip
    except Exception:
        pass

    # Final fallback
    try:
        hostname = socket.gethostname()
        ip = socket.getaddrinfo(hostname, None, socket.AF_INET)[0][4][0]
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    return "127.0.0.1"


class Network:
    """
    Thin wrapper over kademlia.Server with a periodic register/refresh loop.
    Created and run from the chat app's asyncio thread.
    """

    def __init__(
        self,
        dht_port:  int,
        bootstrap: str = "",
        my_specialty: Optional[str] = None,
        my_label:     Optional[str] = None,
        my_http_port: Optional[int] = None,
    ) -> None:
        self.dht_port      = dht_port
        self.bootstrap     = bootstrap.strip()
        self.my_specialty  = my_specialty
        self.my_label      = my_label
        self.my_http_port  = my_http_port
        self._server       = Server()
        self._running      = False
        # stable node id for deduplication (based on our listen address)
        self._node_id      = (
            f"{_local_ip()}:{my_http_port}" if my_http_port else None
        )
        self._lan_sock: Optional[socket.socket] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        await self._server.listen(self.dht_port)
        if self.bootstrap:
            host, _, port = self.bootstrap.partition(":")
            try:
                await self._server.bootstrap([(host, int(port or 8468))])
                print(f"[network] bootstrapped via {host}:{port or 8468}")
            except Exception as exc:
                print(f"[network] bootstrap to {self.bootstrap} failed: {exc}")
        self._running = True
        await self._publish_self()  # ensure we appear in the peer list immediately
        asyncio.create_task(self._refresh_loop())
        # If no bootstrap, also do UDP LAN broadcast for local discovery
        if not self.bootstrap:
            self._start_lan_broadcast()

    async def stop(self) -> None:
        self._running = False
        self._server.stop()
        if self._lan_sock:
            try:
                self._lan_sock.close()
            except Exception:
                pass

    # ── Register/refresh ──────────────────────────────────────────────────

    async def _refresh_loop(self) -> None:
        """Re-publish our own entry every 30 s so it stays in the DHT."""
        while self._running:
            try:
                await self._publish_self()
            except Exception as exc:
                print(f"[network] publish failed: {exc}")
            await asyncio.sleep(30)

    async def _publish_self(self) -> None:
        if not self.my_specialty or not self.my_http_port:
            return
        url   = f"http://{_local_ip()}:{self.my_http_port}"
        entry = Peer(
            specialty=self.my_specialty,
            label=self.my_label or self.my_specialty.upper(),
            url=url,
            last_seen=time.time(),
        ).to_dict()

        # Read-modify-write the shared peer list (best-effort CAS)
        raw   = await self._server.get(PEER_LIST_KEY)
        peers = json.loads(raw) if raw else []
        # Deduplicate by URL — last write wins for this node
        peers = [p for p in peers if p.get("url") != url]
        peers.append(entry)
        # Drop expired entries
        cutoff = time.time() - PEER_TTL_SEC
        peers  = [p for p in peers if float(p.get("last_seen", 0)) >= cutoff]

        await self._server.set(PEER_LIST_KEY, json.dumps(peers))

    # ── LAN Broadcast (bootstrap-less LAN discovery) ────────────────────────

    def _start_lan_broadcast(self) -> None:
        """Broadcast UDP beacons on the LAN so peers find each other
        without a bootstrap node.  Purely additive — doesn't replace DHT."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setblocking(False)
            self._lan_sock = sock
            asyncio.create_task(self._lan_broadcast_loop())
            asyncio.create_task(self._lan_listen_loop())
        except Exception as exc:
            print(f"[network] LAN broadcast setup failed: {exc}")

    async def _lan_broadcast_loop(self) -> None:
        """Send a UDP beacon every 5 seconds."""
        beacon = json.dumps({
            "type": "moe_beacon",
            "dht_port": self.dht_port,
            "specialty": self.my_specialty,
            "label": self.my_label,
        }).encode()
        while self._running and self._lan_sock:
            try:
                self._lan_sock.sendto(beacon, ("<broadcast>", self.dht_port))
            except Exception:
                pass
            await asyncio.sleep(5)

    async def _lan_listen_loop(self) -> None:
        """Listen for incoming beacons and bootstrap to their senders."""
        try:
            self._lan_sock.bind(("0.0.0.0", self.dht_port))
        except Exception:
            return
        while self._running and self._lan_sock:
            try:
                data, addr = self._lan_sock.recvfrom(1024)
                msg = json.loads(data.decode())
                if msg.get("type") == "moe_beacon":
                    peer_ip = addr[0]
                    peer_port = msg.get("dht_port", self.dht_port)
                    # Don't bootstrap to ourselves
                    if peer_ip != _local_ip():
                        try:
                            await self._server.bootstrap([(peer_ip, peer_port)])
                            print(f"[network] LAN peer discovered: {peer_ip}:{peer_port}")
                        except Exception:
                            pass
            except BlockingIOError:
                await asyncio.sleep(0.1)
            except Exception:
                await asyncio.sleep(1)

    # ── Discovery ─────────────────────────────────────────────────────────

    async def discover(self) -> list[Peer]:
        """Return all currently-known peers, freshest first."""
        try:
            raw = await self._server.get(PEER_LIST_KEY)
        except Exception:
            return []
        if not raw:
            return []
        try:
            peers = [Peer.from_dict(d) for d in json.loads(raw)]
        except Exception:
            return []
        cutoff = time.time() - PEER_TTL_SEC
        peers  = [p for p in peers if p.last_seen >= cutoff]
        # Deduplicate by URL (in case stale entries slipped in)
        seen: set[str] = set()
        unique: list[Peer] = []
        for p in peers:
            if p.url not in seen:
                seen.add(p.url)
                unique.append(p)
        unique.sort(key=lambda p: p.last_seen, reverse=True)
        return unique
