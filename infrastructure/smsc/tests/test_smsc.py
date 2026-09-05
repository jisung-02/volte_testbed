"""SMSC handler logic tests (no real network)."""
import pytest

from infrastructure.smsc.smsc import SmscHandler
from infrastructure.smsc.sip import parse_sip_message


SAMPLE_MO_MESSAGE = (
    b"MESSAGE sip:smsc.ims.mnc001.mcc001.3gppnetwork.org SIP/2.0\r\n"
    b"Via: SIP/2.0/UDP 172.22.0.20:5060;branch=z9hG4bK-abc123\r\n"
    b"From: <sip:222222@ims.mnc001.mcc001.3gppnetwork.org>;tag=mo-tag\r\n"
    b"To: <sip:111111@ims.mnc001.mcc001.3gppnetwork.org>\r\n"
    b"Call-ID: call-1234@scscf\r\n"
    b"CSeq: 1 MESSAGE\r\n"
    b"Max-Forwards: 70\r\n"
    b"Content-Type: application/vnd.3gpp.sms\r\n"
    b"Content-Length: 24\r\n"
    b"\r\n"
    # MO RP-DATA (MS→Network): direction=0x00, ref=0x00, RP-OA=empty (0x00),
    # RP-DA=encode_address("9999")=\x04\x91\x99\x99, RP-UD tag=\x41, UDL=\x0f,
    # then SMS-SUBMIT TPDU for "hello" to destination "111111".
    # Generated via tpdu.wrap_rp_data(MS_TO_NETWORK, ref=0, tpdu=submit, smsc_msisdn="9999")
    b"\x00\x00\x00\x04\x91\x99\x99\x41\x0f"
    b"\x01\x00\x06\x91\x11\x11\x11\x00\x00\x05\xe8\x32\x9b\xfd\x06"
)


def test_handler_produces_mt_for_valid_mo():
    sent = []
    handler = SmscHandler(
        smsc_ip="172.22.0.27",
        smsc_msisdn="9999",
        icscf_addr=("172.22.0.19", 5060),
        send_callback=lambda data, addr: sent.append((data, addr)),
    )

    handler.handle_packet(SAMPLE_MO_MESSAGE, ("172.22.0.20", 5060))

    assert len(sent) == 1, "expected MT only, MO 200 must wait for MT response"
    assert sent[0][0].startswith(b"MESSAGE ")
    assert sent[0][1] == ("172.22.0.19", 5060)
    mt = parse_sip_message(sent[0][0])
    assert "111111" in mt.request_uri
    assert "222222" in mt.header("from")


def test_handler_rejects_non_message_with_405():
    sent = []
    handler = SmscHandler(
        smsc_ip="172.22.0.27",
        smsc_msisdn="9999",
        icscf_addr=("172.22.0.19", 5060),
        send_callback=lambda data, addr: sent.append((data, addr)),
    )

    options_request = (
        b"OPTIONS sip:smsc.ims.mnc001.mcc001.3gppnetwork.org SIP/2.0\r\n"
        b"Via: SIP/2.0/UDP 172.22.0.20:5060;branch=z9hG4bK-xyz\r\n"
        b"From: <sip:probe@example.com>;tag=p1\r\n"
        b"To: <sip:smsc@ims.mnc001.mcc001.3gppnetwork.org>\r\n"
        b"Call-ID: probe-1\r\n"
        b"CSeq: 1 OPTIONS\r\n"
        b"Content-Length: 0\r\n"
        b"\r\n"
    )

    handler.handle_packet(options_request, ("172.22.0.20", 5060))

    assert len(sent) == 1
    assert sent[0][0].startswith(b"SIP/2.0 405 Method Not Allowed\r\n")


def test_mo_response_deferred_until_mt_response():
    """MO 200 OK must wait for MT response (no immediate 200)."""
    sent = []
    handler = SmscHandler(
        smsc_ip="172.22.0.27",
        smsc_msisdn="9999",
        icscf_addr=("172.22.0.19", 5060),
        send_callback=lambda data, addr: sent.append((data, addr)),
    )

    handler.handle_packet(SAMPLE_MO_MESSAGE, ("172.22.0.20", 5060))

    # Only the MT MESSAGE should be sent so far — no MO response yet
    assert len(sent) == 1
    assert sent[0][0].startswith(b"MESSAGE ")
    assert sent[0][1] == ("172.22.0.19", 5060)

    # Capture the MT call-id from outgoing packet
    mt_msg = parse_sip_message(sent[0][0])
    mt_call_id = mt_msg.header("call-id")

    # Simulate MT 200 OK arriving from I-CSCF
    mt_response = (
        b"SIP/2.0 200 OK\r\n"
        + f"Via: {mt_msg.header('via')}\r\n".encode()
        + f"From: {mt_msg.header('from')}\r\n".encode()
        + f"To: {mt_msg.header('to')};tag=recipient-tag\r\n".encode()
        + f"Call-ID: {mt_call_id}\r\n".encode()
        + f"CSeq: {mt_msg.header('cseq')}\r\n".encode()
        + b"Content-Length: 0\r\n\r\n"
    )

    handler.handle_packet(mt_response, ("172.22.0.19", 5060))

    # Now MO 200 OK should be sent
    assert len(sent) == 2
    assert sent[1][0].startswith(b"SIP/2.0 200 OK\r\n")
    assert sent[1][1] == ("172.22.0.20", 5060)
    # MO response must echo MO request's Call-ID
    assert b"Call-ID: call-1234@scscf\r\n" in sent[1][0]


def test_mt_480_maps_to_mo_480():
    sent = []
    handler = SmscHandler(
        smsc_ip="172.22.0.27",
        smsc_msisdn="9999",
        icscf_addr=("172.22.0.19", 5060),
        send_callback=lambda data, addr: sent.append((data, addr)),
    )

    handler.handle_packet(SAMPLE_MO_MESSAGE, ("172.22.0.20", 5060))
    mt_msg = parse_sip_message(sent[0][0])

    mt_response = (
        b"SIP/2.0 480 Temporarily Unavailable\r\n"
        + f"Via: {mt_msg.header('via')}\r\n".encode()
        + f"Call-ID: {mt_msg.header('call-id')}\r\n".encode()
        + f"CSeq: {mt_msg.header('cseq')}\r\n".encode()
        + b"Content-Length: 0\r\n\r\n"
    )
    handler.handle_packet(mt_response, ("172.22.0.19", 5060))

    assert len(sent) == 2
    assert sent[1][0].startswith(b"SIP/2.0 480 Temporarily Unavailable\r\n")


def test_mt_500_maps_to_mo_500():
    sent = []
    handler = SmscHandler(
        smsc_ip="172.22.0.27",
        smsc_msisdn="9999",
        icscf_addr=("172.22.0.19", 5060),
        send_callback=lambda data, addr: sent.append((data, addr)),
    )

    handler.handle_packet(SAMPLE_MO_MESSAGE, ("172.22.0.20", 5060))
    mt_msg = parse_sip_message(sent[0][0])

    mt_response = (
        b"SIP/2.0 503 Service Unavailable\r\n"
        + f"Via: {mt_msg.header('via')}\r\n".encode()
        + f"Call-ID: {mt_msg.header('call-id')}\r\n".encode()
        + f"CSeq: {mt_msg.header('cseq')}\r\n".encode()
        + b"Content-Length: 0\r\n\r\n"
    )
    handler.handle_packet(mt_response, ("172.22.0.19", 5060))

    assert sent[1][0].startswith(b"SIP/2.0 500 Server Internal Error\r\n")


def test_mt_timeout_fires_408_to_mo(transaction_handler):
    """Provisional MT responses must not complete or extend the delivery deadline."""
    handler, sent, now = transaction_handler
    handler.handle_packet(SAMPLE_MO_MESSAGE, ("172.22.0.20", 5060))
    now[0] += 31
    handler.handle_packet(mt_response(sent[0][0], 100), ("172.22.0.19", 5060))
    assert len(sent) == 1
    now[0] += 1
    handler.sweep_expired()
    assert len(sent) == 2
    assert sent[1][0].startswith(b"SIP/2.0 408 Request Timeout\r\n")


def test_handler_rejects_bad_tpdu_with_400():
    sent = []
    handler = SmscHandler(
        smsc_ip="172.22.0.27",
        smsc_msisdn="9999",
        icscf_addr=("172.22.0.19", 5060),
        send_callback=lambda data, addr: sent.append((data, addr)),
    )

    bad_body_message = (
        b"MESSAGE sip:smsc.ims.mnc001.mcc001.3gppnetwork.org SIP/2.0\r\n"
        b"Via: SIP/2.0/UDP 172.22.0.20:5060;branch=z9hG4bK-bad\r\n"
        b"From: <sip:222222@ims.mnc001.mcc001.3gppnetwork.org>;tag=t\r\n"
        b"To: <sip:111111@ims.mnc001.mcc001.3gppnetwork.org>\r\n"
        b"Call-ID: bad-1\r\n"
        b"CSeq: 1 MESSAGE\r\n"
        b"Content-Type: application/vnd.3gpp.sms\r\n"
        b"Content-Length: 4\r\n"
        b"\r\n"
        b"\xff\xff\xff\xff"  # garbage RP-DATA
    )

    handler.handle_packet(bad_body_message, ("172.22.0.20", 5060))

    assert len(sent) == 1
    assert sent[0][0].startswith(b"SIP/2.0 400 Bad Request\r\n")


def test_handler_accepts_content_type_with_charset_param():
    """Real UEs may send `application/vnd.3gpp.sms;charset=utf-8` etc. — must not 415."""
    sent = []
    handler = SmscHandler(
        smsc_ip="172.22.0.27",
        smsc_msisdn="9999",
        icscf_addr=("172.22.0.19", 5060),
        send_callback=lambda data, addr: sent.append((data, addr)),
    )
    # Use SAMPLE_MO_MESSAGE but with Content-Type that has a charset param
    mo = SAMPLE_MO_MESSAGE.replace(
        b"Content-Type: application/vnd.3gpp.sms\r\n",
        b"Content-Type: application/vnd.3gpp.sms;charset=utf-8\r\n",
    )
    handler.handle_packet(mo, ("172.22.0.20", 5060))
    # Should produce MT, not 415
    mt_pkts = [p for p in sent if p[0].startswith(b"MESSAGE ")]
    response_pkts = [p for p in sent if p[0].startswith(b"SIP/2.0 415")]
    assert len(mt_pkts) == 1, f"expected MT MESSAGE, got: {[p[0][:50] for p in sent]}"
    assert len(response_pkts) == 0


@pytest.fixture
def transaction_handler(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("infrastructure.smsc.smsc.time.monotonic", lambda: now[0])
    sent = []
    handler = SmscHandler("172.22.0.27", "9999", ("172.22.0.19", 5060),
                          lambda data, addr: sent.append((data, addr)))
    return handler, sent, now


def mt_response(mt_bytes, status):
    mt = parse_sip_message(mt_bytes)
    return (f"SIP/2.0 {status} Test\r\nCall-ID: {mt.header('call-id')}\r\n"
            "CSeq: 1 MESSAGE\r\nContent-Length: 0\r\n\r\n").encode()


@pytest.mark.parametrize("status", [100, 180, 199])
def test_provisional_mt_waits_for_final_response(transaction_handler, status):
    handler, sent, _ = transaction_handler
    handler.handle_packet(SAMPLE_MO_MESSAGE, ("172.22.0.20", 5060))
    mt = sent[0][0]
    handler.handle_packet(mt_response(mt, status), ("172.22.0.19", 5060))
    assert len(sent) == 1
    handler.handle_packet(mt_response(mt, 200), ("172.22.0.19", 5060))
    assert parse_sip_message(sent[1][0]).status_code == 200


def test_pending_retransmission_sends_only_one_mt(transaction_handler):
    handler, sent, now = transaction_handler
    sender = ("172.22.0.20", 5060)
    handler.handle_packet(SAMPLE_MO_MESSAGE, sender)
    now[0] += 31
    handler.handle_packet(SAMPLE_MO_MESSAGE, sender)
    assert len(sent) == 1
    now[0] += 1
    handler.sweep_expired()
    assert parse_sip_message(sent[1][0]).status_code == 408


@pytest.mark.parametrize("status", [200, 480, 503, None])
def test_completed_retransmission_replays_until_expiry(transaction_handler, status):
    handler, sent, now = transaction_handler
    sender = ("172.22.0.20", 5060)
    mo = SAMPLE_MO_MESSAGE.replace(b"From:", b"Via: SIP/2.0/UDP proxy:5060;branch=z9hG4bK-lower\r\nFrom:")
    handler.handle_packet(mo, sender)
    mt = sent[0][0]
    if status is None:
        now[0] += 32
        handler.sweep_expired()
    else:
        handler.handle_packet(mt_response(mt, status), ("172.22.0.19", 5060))
    final = sent[1]
    assert parse_sip_message(final[0]).headers["via"] == parse_sip_message(mo).headers["via"]
    now[0] += 31
    handler.handle_packet(mo, sender)
    assert sent[2] == final
    handler.handle_packet(mt_response(mt, 200), ("172.22.0.19", 5060))
    assert len(sent) == 3
    now[0] += 1
    handler.handle_packet(mo, sender)
    assert len(sent) == 4
    assert sent[-1][0].startswith(b"MESSAGE ")
    assert parse_sip_message(sent[-1][0]).header("call-id") != parse_sip_message(mt).header("call-id")


@pytest.mark.parametrize("old,new,sender", [
    (b"branch=z9hG4bK-abc123", b"branch=z9hG4bK-new", ("172.22.0.20", 5060)),
    (b"172.22.0.20:5060", b"other-proxy:5060", ("172.22.0.20", 5060)),
    (b"call-1234@scscf", b"call-new@scscf", ("172.22.0.20", 5060)),
    (b"CSeq: 1 MESSAGE", b"CSeq: 2 MESSAGE", ("172.22.0.20", 5060)),
    (b"unused", b"unused", ("172.22.0.21", 5060)),
])
def test_fresh_mo_transaction_is_not_suppressed(transaction_handler, old, new, sender):
    handler, sent, _ = transaction_handler
    handler.handle_packet(SAMPLE_MO_MESSAGE, ("172.22.0.20", 5060))
    handler.handle_packet(SAMPLE_MO_MESSAGE.replace(old, new), sender)
    assert len(sent) == 2
    assert all(data.startswith(b"MESSAGE ") for data, _ in sent)


def test_via_routing_parameters_do_not_change_transaction(transaction_handler):
    handler, sent, _ = transaction_handler
    sender = ("172.22.0.20", 5060)
    handler.handle_packet(SAMPLE_MO_MESSAGE, sender)
    retransmit = SAMPLE_MO_MESSAGE.replace(b";branch=z9hG4bK-abc123", b";received=172.22.0.20;branch=z9hG4bK-abc123;rport=5060")
    handler.handle_packet(retransmit, sender)
    assert len(sent) == 1
