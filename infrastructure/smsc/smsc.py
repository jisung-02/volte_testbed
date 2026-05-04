"""SMSC main: asyncio UDP listener + MO/MT handler.

This file owns the SmscHandler class (pure logic, testable without a real socket)
and the asyncio app entrypoint that wires the handler to a real UDP socket.

Import strategy
---------------
``from sip import ...`` and ``from tpdu import ...`` use bare module names (no
package prefix) so that the file works when run directly inside the Docker
container::

    # Dockerfile: CMD ["python", "smsc.py"]
    # /app/ contains smsc.py, sip.py, tpdu.py flat — bare imports resolve fine.

For pytest, ``volte_testbed/infrastructure/smsc/tests/conftest.py`` inserts
``infrastructure/smsc/`` into sys.path before collection, so the bare names
resolve to the same physical files.  Tests themselves still use
``from infrastructure.smsc.smsc import SmscHandler`` (package-style), which is
fine because Python treats module identity by sys.path lookup, not file path.
"""
import asyncio
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from sip import SipMessage, parse_sip_message, build_sip_response, build_mt_message, extract_msisdn_from_uri
from tpdu import swap_mo_to_mt


log = logging.getLogger("smsc")

SmscSendCallback = Callable[[bytes, tuple[str, int]], None]


@dataclass
class _InFlight:
    mo_request: SipMessage
    mo_sender: tuple[str, int]
    expires_at: float


def _map_mt_status_to_mo(mt_status: int) -> tuple[int, str]:
    """Per spec Section 5: MT response → MO response."""
    if 200 <= mt_status < 300:
        return 200, "OK"
    if mt_status in (404, 480, 486):
        return 480, "Temporarily Unavailable"
    if 500 <= mt_status < 700:
        return 500, "Server Internal Error"
    # Other 4xx (incl. 4xx not enumerated): pass through 480 as conservative default
    return 480, "Temporarily Unavailable"


class SmscHandler:
    """Pure logic: takes inbound bytes, produces outbound bytes via send_callback.

    No socket ownership — that's the asyncio Protocol's job."""

    def __init__(
        self,
        smsc_ip: str,
        smsc_msisdn: str,
        icscf_addr: tuple[str, int],
        send_callback: SmscSendCallback,
        in_flight_ttl_seconds: float = 32.0,
    ) -> None:
        self._smsc_ip = smsc_ip
        self._smsc_msisdn = smsc_msisdn
        self._icscf_addr = icscf_addr
        self._send = send_callback
        self._ttl = in_flight_ttl_seconds
        self._in_flight: dict[str, _InFlight] = {}  # keyed by MT Call-ID

    def handle_packet(self, data: bytes, sender: tuple[str, int]) -> None:
        try:
            msg = parse_sip_message(data)
        except Exception as exc:
            log.warning("failed to parse SIP from %s: %s", sender, exc)
            return

        if msg.status_code != 0:
            self._handle_mt_response(msg)
            return

        if msg.method != "MESSAGE":
            self._send(build_sip_response(msg, 405, "Method Not Allowed"), sender)
            return

        self._handle_mo_message(msg, sender)

    def _handle_mo_message(self, msg: SipMessage, sender: tuple[str, int]) -> None:
        content_type = msg.headers.get("content-type", "")
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type != "application/vnd.3gpp.sms":
            self._send(build_sip_response(msg, 415, "Unsupported Media Type"), sender)
            return

        try:
            sender_msisdn = extract_msisdn_from_uri(msg.headers.get("from", ""))
            recipient, mt_rp = swap_mo_to_mt(
                mo_rp_data=msg.body,
                sender_msisdn=sender_msisdn,
                smsc_msisdn=self._smsc_msisdn,
            )
        except Exception as exc:
            log.warning("MO TPDU decode failed (call-id=%s): %s", msg.headers.get("call-id"), exc, exc_info=True)
            self._send(build_sip_response(msg, 400, "Bad Request"), sender)
            return

        mt_call_id = f"smsc-{secrets.token_hex(8)}@smsc.ims.mnc001.mcc001.3gppnetwork.org"
        mt_bytes = build_mt_message(
            recipient_msisdn=recipient,
            sender_msisdn=sender_msisdn,
            smsc_host="smsc.ims.mnc001.mcc001.3gppnetwork.org",
            smsc_ip=self._smsc_ip,
            smsc_port=5060,
            call_id=mt_call_id,
            body=mt_rp,
        )

        self._in_flight[mt_call_id] = _InFlight(
            mo_request=msg,
            mo_sender=sender,
            expires_at=time.monotonic() + self._ttl,
        )
        self._send(mt_bytes, self._icscf_addr)

    def _handle_mt_response(self, response: SipMessage) -> None:
        call_id = response.headers.get("call-id", "")
        in_flight = self._in_flight.pop(call_id, None)
        if in_flight is None:
            log.debug("late or unknown MT response %d for call-id=%s — dropped", response.status_code, call_id)
            return
        mo_status, mo_reason = _map_mt_status_to_mo(response.status_code)
        self._send(
            build_sip_response(in_flight.mo_request, mo_status, mo_reason),
            in_flight.mo_sender,
        )

    def sweep_expired(self) -> None:
        """Send 408 Request Timeout for any MO transaction whose MT correlation has expired."""
        now = time.monotonic()
        expired = [(cid, ent) for cid, ent in self._in_flight.items() if ent.expires_at <= now]
        for cid, ent in expired:
            del self._in_flight[cid]
            log.warning("MT timeout for MO call-id=%s", ent.mo_request.headers.get("call-id"))
            self._send(
                build_sip_response(ent.mo_request, 408, "Request Timeout"),
                ent.mo_sender,
            )


class SmscProtocol(asyncio.DatagramProtocol):
    def __init__(self, handler: "SmscHandler | _PlaceholderHandler") -> None:
        self._handler = handler
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._handler.handle_packet(data, addr)


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("SMSC_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    smsc_ip = os.environ["SMSC_IP"]
    icscf_ip = os.environ["ICSCF_IP"]
    smsc_msisdn = os.environ.get("SMSC_MSISDN", "9999")

    loop = asyncio.get_running_loop()

    def make_send(transport: asyncio.DatagramTransport) -> SmscSendCallback:
        def send(data: bytes, addr: tuple[str, int]) -> None:
            transport.sendto(data, addr)
        return send

    # Bind socket first, then construct handler with its sendto.
    # A placeholder handler absorbs any packets arriving before the real
    # handler is constructed (practically zero window on a local socket).
    placeholder = _PlaceholderHandler()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: SmscProtocol(handler=placeholder),
        local_addr=(smsc_ip, 5060),
    )
    handler = SmscHandler(
        smsc_ip=smsc_ip,
        smsc_msisdn=smsc_msisdn,
        icscf_addr=(icscf_ip, 5060),
        send_callback=make_send(transport),
    )
    # Swap the placeholder for the real handler now that we have the transport.
    protocol._handler = handler  # type: ignore[attr-defined]

    log.info(
        "SMSC listening on %s:5060, ICSCF=%s:5060, MSISDN=%s",
        smsc_ip,
        icscf_ip,
        smsc_msisdn,
    )

    async def sweep_loop():
        while True:
            await asyncio.sleep(1.0)
            try:
                handler.sweep_expired()
            except Exception:
                log.exception("sweep_expired raised unexpectedly")

    sweep_task = asyncio.create_task(sweep_loop())
    try:
        await asyncio.Event().wait()
    finally:
        sweep_task.cancel()
        transport.close()


class _PlaceholderHandler:
    """Used during socket creation before the real handler can be constructed."""

    def handle_packet(self, data: bytes, sender: tuple[str, int]) -> None:
        log.warning("dropped packet during initialization from %s", sender)


if __name__ == "__main__":
    asyncio.run(main())
