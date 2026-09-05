"""UDP SMSC with pending MT correlation and short-lived MO response replay.

Bare imports match the Docker image's flat /app layout; tests add this directory
to sys.path. Protocol scope and limitations are recorded in docs/testing.md.
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
class _MoTransaction:
    mo_request: SipMessage
    mo_sender: tuple[str, int]
    expires_at: float
    response: bytes | None = None


def _map_mt_status_to_mo(mt_status: int) -> tuple[int, str]:
    """Map final MT status to the testbed's MO delivery result."""
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
        self._in_flight: dict[str, _MoTransaction] = {}  # keyed by MT Call-ID
        self._mo_transactions: dict[tuple, _MoTransaction] = {}

    def handle_packet(self, data: bytes, sender: tuple[str, int]) -> None:
        self.sweep_expired()
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
        # Only the top Via identifies this hop's transaction; received/rport
        # and lower proxy Vias do not create a new transaction.
        top_via = msg.header("via").split(",", 1)[0]
        sent_by, *parameters = top_via.split(";")
        branch = ""
        for parameter in parameters:
            name, sep, value = parameter.partition("=")
            if sep and name.strip().lower() == "branch":
                branch = value.strip()
                break
        key = (sender, sent_by.strip(), branch or top_via,
               msg.header("call-id"), msg.header("cseq"))
        previous = self._mo_transactions.get(key)
        if previous is not None:
            if previous.response is not None:
                self._send(previous.response, sender)
            return

        content_type = msg.header("content-type")
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type != "application/vnd.3gpp.sms":
            self._send(build_sip_response(msg, 415, "Unsupported Media Type"), sender)
            return

        try:
            sender_msisdn = extract_msisdn_from_uri(msg.header("from"))
            recipient, mt_rp = swap_mo_to_mt(
                mo_rp_data=msg.body,
                sender_msisdn=sender_msisdn,
                smsc_msisdn=self._smsc_msisdn,
            )
        except Exception as exc:
            log.warning("MO TPDU decode failed (call-id=%s): %s", msg.header("call-id"), exc, exc_info=True)
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

        transaction = _MoTransaction(
            mo_request=msg,
            mo_sender=sender,
            expires_at=time.monotonic() + self._ttl,
        )
        self._in_flight[mt_call_id] = transaction
        self._mo_transactions[key] = transaction
        self._send(mt_bytes, self._icscf_addr)

    def _handle_mt_response(self, response: SipMessage) -> None:
        if 100 <= response.status_code < 200:
            return
        call_id = response.header("call-id")
        in_flight = self._in_flight.pop(call_id, None)
        if in_flight is None:
            log.debug("late or unknown MT response %d for call-id=%s — dropped", response.status_code, call_id)
            return
        mo_status, mo_reason = _map_mt_status_to_mo(response.status_code)
        self._complete_mo(in_flight, mo_status, mo_reason)

    def _complete_mo(self, transaction: _MoTransaction, status: int, reason: str) -> None:
        transaction.response = build_sip_response(transaction.mo_request, status, reason)
        transaction.expires_at = time.monotonic() + 32.0
        self._send(transaction.response, transaction.mo_sender)

    def sweep_expired(self) -> None:
        """Time out pending MT delivery and discard expired MO response replays."""
        now = time.monotonic()
        expired = [(cid, ent) for cid, ent in self._in_flight.items() if ent.expires_at <= now]
        for cid, ent in expired:
            del self._in_flight[cid]
            log.warning("MT timeout for MO call-id=%s", ent.mo_request.header("call-id"))
            self._complete_mo(ent, 408, "Request Timeout")
        expired_mo = [key for key, ent in self._mo_transactions.items()
                      if ent.response is not None and ent.expires_at <= now]
        for key in expired_mo:
            del self._mo_transactions[key]


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
