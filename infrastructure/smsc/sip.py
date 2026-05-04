"""Minimal SIP MESSAGE parser/builder for SMSC use.

Scope: MESSAGE method requests + status responses only. No dialog, no transaction state,
no support for INVITE/REGISTER/etc."""
import re
import secrets
from dataclasses import dataclass, field


@dataclass
class SipMessage:
    method: str                          # e.g. "MESSAGE" — empty for responses
    request_uri: str                     # empty for responses
    status_code: int = 0                 # nonzero for responses
    reason_phrase: str = ""
    headers: dict[str, str] = field(default_factory=dict)  # lowercase keys
    body: bytes = b""


_URI_MSISDN_RE = re.compile(r"sip:(\+?\d+)@", re.IGNORECASE)


def extract_msisdn_from_uri(value: str) -> str:
    """Pull the user portion of a sip: URI when it looks like an MSISDN."""
    m = _URI_MSISDN_RE.search(value)
    if not m:
        raise ValueError(f"no MSISDN found in {value!r}")
    return m.group(1).lstrip("+")


def parse_sip_message(data: bytes) -> SipMessage:
    """Parse a SIP request or response. Headers are lowercased; body is raw bytes."""
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    start_line = lines[0].decode("utf-8", errors="replace")

    msg = SipMessage(method="", request_uri="", body=body)

    if start_line.startswith("SIP/"):
        # Response: "SIP/2.0 200 OK"
        parts = start_line.split(" ", 2)
        msg.status_code = int(parts[1])
        msg.reason_phrase = parts[2] if len(parts) > 2 else ""
    else:
        # Request: "MESSAGE sip:... SIP/2.0"
        method, uri, _ = start_line.split(" ", 2)
        msg.method = method
        msg.request_uri = uri

    # NOTE: RFC 3261 folded headers (continuation lines starting with SP/HTAB)
    # are not supported. Kamailio does not emit them; the silent drop is
    # acceptable for testbed traffic. Fix at parser level if a real UE ever
    # sends folded headers via the IMS path.
    for line in lines[1:]:
        decoded = line.decode("utf-8", errors="replace")
        if ":" not in decoded:
            continue
        name, value = decoded.split(":", 1)
        msg.headers[name.strip().lower()] = value.strip()

    return msg


_DOMAIN = "ims.mnc001.mcc001.3gppnetwork.org"
_RESPONSE_HEADERS_TO_COPY = ("via", "from", "to", "call-id", "cseq")


def build_sip_response(request: SipMessage, status_code: int, reason: str) -> bytes:
    """Build a SIP response that echoes the routing-relevant request headers."""
    lines = [f"SIP/2.0 {status_code} {reason}".encode()]
    for header_name in _RESPONSE_HEADERS_TO_COPY:
        if header_name in request.headers:
            # Capitalize header name in response (cosmetic)
            display_name = "-".join(part.capitalize() for part in header_name.split("-"))
            if header_name == "call-id":
                display_name = "Call-ID"
            elif header_name == "cseq":
                display_name = "CSeq"
            lines.append(f"{display_name}: {request.headers[header_name]}".encode())
    lines.append(b"Content-Length: 0")
    return b"\r\n".join(lines) + b"\r\n\r\n"


def build_mt_message(
    recipient_msisdn: str,
    sender_msisdn: str,
    smsc_host: str,
    smsc_ip: str,
    smsc_port: int,
    call_id: str,
    body: bytes,
) -> bytes:
    """Build a fresh MT MESSAGE request originating from SMSC."""
    branch = "z9hG4bK-" + secrets.token_hex(8)
    tag = secrets.token_hex(4)
    headers = [
        f"MESSAGE sip:{recipient_msisdn}@{_DOMAIN} SIP/2.0",
        f"Via: SIP/2.0/UDP {smsc_ip}:{smsc_port};branch={branch}",
        f"From: <sip:{sender_msisdn}@{_DOMAIN}>;tag={tag}",
        f"To: <sip:{recipient_msisdn}@{_DOMAIN}>",
        f"Call-ID: {call_id}",
        "CSeq: 1 MESSAGE",
        "Max-Forwards: 70",
        "Content-Type: application/vnd.3gpp.sms",
        f"Content-Length: {len(body)}",
    ]
    return ("\r\n".join(headers) + "\r\n\r\n").encode() + body
