"""SMSC handler logic tests (no real network)."""
import time

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
    assert "222222" in mt.headers["from"]


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
    mt_call_id = mt_msg.headers["call-id"]

    # Simulate MT 200 OK arriving from I-CSCF
    mt_response = (
        b"SIP/2.0 200 OK\r\n"
        + f"Via: {mt_msg.headers['via']}\r\n".encode()
        + f"From: {mt_msg.headers['from']}\r\n".encode()
        + f"To: {mt_msg.headers['to']};tag=recipient-tag\r\n".encode()
        + f"Call-ID: {mt_call_id}\r\n".encode()
        + f"CSeq: {mt_msg.headers['cseq']}\r\n".encode()
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
        + f"Via: {mt_msg.headers['via']}\r\n".encode()
        + f"Call-ID: {mt_msg.headers['call-id']}\r\n".encode()
        + f"CSeq: {mt_msg.headers['cseq']}\r\n".encode()
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
        + f"Via: {mt_msg.headers['via']}\r\n".encode()
        + f"Call-ID: {mt_msg.headers['call-id']}\r\n".encode()
        + f"CSeq: {mt_msg.headers['cseq']}\r\n".encode()
        + b"Content-Length: 0\r\n\r\n"
    )
    handler.handle_packet(mt_response, ("172.22.0.19", 5060))

    assert sent[1][0].startswith(b"SIP/2.0 500 Server Internal Error\r\n")


def test_mt_timeout_fires_408_to_mo():
    """When TTL expires without MT response, MO gets 408 Request Timeout."""
    sent = []
    handler = SmscHandler(
        smsc_ip="172.22.0.27",
        smsc_msisdn="9999",
        icscf_addr=("172.22.0.19", 5060),
        send_callback=lambda data, addr: sent.append((data, addr)),
        in_flight_ttl_seconds=0.1,  # short TTL for testing
    )

    handler.handle_packet(SAMPLE_MO_MESSAGE, ("172.22.0.20", 5060))
    assert len(sent) == 1  # only MT sent

    # Trigger sweep manually (no asyncio loop in unit test)
    time.sleep(0.15)
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
