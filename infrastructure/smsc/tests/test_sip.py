"""SIP MESSAGE parser/builder tests."""
from infrastructure.smsc.sip import parse_sip_message, SipMessage


SAMPLE_MO_MESSAGE = (
    b"MESSAGE sip:111111@ims.mnc001.mcc001.3gppnetwork.org SIP/2.0\r\n"
    b"Via: SIP/2.0/UDP 172.22.0.20:5060;branch=z9hG4bK-abc123\r\n"
    b"From: <sip:222222@ims.mnc001.mcc001.3gppnetwork.org>;tag=mo-tag\r\n"
    b"To: <sip:111111@ims.mnc001.mcc001.3gppnetwork.org>\r\n"
    b"Call-ID: call-1234@scscf\r\n"
    b"CSeq: 1 MESSAGE\r\n"
    b"Max-Forwards: 70\r\n"
    b"Content-Type: application/vnd.3gpp.sms\r\n"
    b"Content-Length: 28\r\n"
    b"\r\n"
    b"\x00\x00\x00\x04\x91\x99\x99\x99\x41\x0f\x01\x00\x06\x91\x11\x11\x11\x00\x00\x05\xc8\x32\x9b\xfd\x06\x00\x00\x00"
)


def test_parse_sip_message_basic():
    msg = parse_sip_message(SAMPLE_MO_MESSAGE)

    assert msg.method == "MESSAGE"
    assert msg.request_uri == "sip:111111@ims.mnc001.mcc001.3gppnetwork.org"
    assert msg.headers["call-id"] == "call-1234@scscf"
    assert msg.headers["cseq"] == "1 MESSAGE"
    assert msg.headers["content-type"] == "application/vnd.3gpp.sms"
    assert "222222" in msg.headers["from"]
    assert msg.body[:2] == b"\x00\x00"  # RP-DATA MTI=0


def test_parse_sip_message_extracts_from_msisdn():
    from infrastructure.smsc.sip import extract_msisdn_from_uri

    assert extract_msisdn_from_uri("<sip:222222@ims.mnc001.mcc001.3gppnetwork.org>;tag=foo") == "222222"
    assert extract_msisdn_from_uri("sip:111111@example.com") == "111111"


from infrastructure.smsc.sip import build_sip_response, build_mt_message


def test_build_sip_response_200():
    request = parse_sip_message(SAMPLE_MO_MESSAGE)
    response = build_sip_response(request, status_code=200, reason="OK")

    assert response.startswith(b"SIP/2.0 200 OK\r\n")
    assert b"Call-ID: call-1234@scscf" in response
    assert b"CSeq: 1 MESSAGE" in response
    assert b"Via: SIP/2.0/UDP 172.22.0.20:5060;branch=z9hG4bK-abc123" in response
    # Response body is empty
    assert response.endswith(b"\r\n\r\n")


def test_build_mt_message():
    body = b"\x01\x00MT-RP-DATA"
    msg_bytes = build_mt_message(
        recipient_msisdn="111111",
        sender_msisdn="222222",
        smsc_host="smsc.ims.mnc001.mcc001.3gppnetwork.org",
        smsc_ip="172.22.0.27",
        smsc_port=5060,
        call_id="mt-call-9876@smsc",
        body=body,
    )

    assert msg_bytes.startswith(b"MESSAGE sip:111111@ims.mnc001.mcc001.3gppnetwork.org SIP/2.0\r\n")
    assert b"Via: SIP/2.0/UDP 172.22.0.27:5060;branch=" in msg_bytes
    assert b"From: <sip:222222@ims.mnc001.mcc001.3gppnetwork.org>" in msg_bytes
    assert b"To: <sip:111111@ims.mnc001.mcc001.3gppnetwork.org>" in msg_bytes
    assert b"Call-ID: mt-call-9876@smsc\r\n" in msg_bytes
    assert b"CSeq: 1 MESSAGE\r\n" in msg_bytes
    assert b"Content-Type: application/vnd.3gpp.sms\r\n" in msg_bytes
    assert f"Content-Length: {len(body)}\r\n".encode() in msg_bytes
    assert msg_bytes.endswith(b"\r\n\r\n" + body)
