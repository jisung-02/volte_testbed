"""TPDU codec tests."""
from datetime import datetime, timezone
from infrastructure.smsc.tpdu import (
    encode_address,
    decode_address,
    pack_septets,
    unpack_septets,
    encode_scts,
)


def test_encode_address_international_msisdn():
    # MSISDN 111111 → length=06, type=0x91, BCD nibble-swapped (palindrome here)
    result = encode_address("111111")
    assert result == bytes.fromhex("0691111111")


def test_encode_address_odd_length():
    # Odd-length MSISDN gets padded with F nibble in high nibble of last byte
    result = encode_address("12345")
    assert result == bytes.fromhex("05912143f5")  # "12345F" swapped: 21 43 F5


def test_decode_address_roundtrip():
    for msisdn in ["111111", "222222", "12345", "12345678901"]:
        encoded = encode_address(msisdn)
        decoded, consumed = decode_address(encoded, 0)
        assert decoded == msisdn, f"roundtrip failed for {msisdn}"
        assert consumed == len(encoded)


def test_pack_septets_hello():
    # "Hello" in GSM 03.38 = septets [72, 101, 108, 108, 111] = [0x48, 0x65, 0x6C, 0x6C, 0x6F]
    septets = bytes([0x48, 0x65, 0x6C, 0x6C, 0x6F])
    packed = pack_septets(septets)
    # Known good packed encoding of "Hello"
    assert packed == bytes.fromhex("C8329BFD06")


def test_unpack_septets_roundtrip():
    septets = bytes([0x48, 0x65, 0x6C, 0x6C, 0x6F])
    packed = pack_septets(septets)
    unpacked = unpack_septets(packed, num_septets=5)
    assert unpacked == septets


def test_encode_scts_known_timestamp():
    # 2026-05-04 12:34:56 UTC (timezone offset = 0)
    ts = datetime(2026, 5, 4, 12, 34, 56, tzinfo=timezone.utc)
    encoded = encode_scts(ts)
    # SCTS is 7 bytes, semi-octet BCD: YY MM DD HH MM SS TZ (each nibble-swapped)
    # 26 05 04 12 34 56 00 → swapped: 62 50 40 21 43 65 00
    assert encoded == bytes.fromhex("62504021436500")
    assert len(encoded) == 7


def test_encode_scts_negative_timezone():
    """UTC-5 (offset = -300 min, 20 quarters) should encode TZ byte with sign bit set, no invalid BCD nibbles."""
    from datetime import timedelta
    ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    encoded = encode_scts(ts)
    assert len(encoded) == 7
    # Verify all bytes contain only valid BCD nibbles (0-9 in each nibble) — except the TZ byte
    # where bit 3 of low nibble carries the sign and may yield 0x08-0x0F in low nibble.
    for b in encoded[:6]:
        assert (b & 0x0F) <= 9, f"date byte 0x{b:02x} has invalid BCD low nibble"
        assert (b >> 4) <= 9, f"date byte 0x{b:02x} has invalid BCD high nibble"
    # TZ byte: tz_quarters=20 → BCD=((20 % 10) << 4) | (20 // 10) = (0 << 4) | 2 = 0x02. With sign bit set: 0x02 | 0x08 = 0x0A
    assert encoded[6] == 0x0A, f"expected TZ byte 0x0A (UTC-5 with sign), got 0x{encoded[6]:02x}"


def test_encode_scts_positive_timezone():
    """UTC+9 (KST) — offset = +540 min, 36 quarters. No sign bit."""
    from datetime import timedelta
    ts = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone(timedelta(hours=9)))
    encoded = encode_scts(ts)
    # tz_quarters=36 → BCD=((36 % 10) << 4) | (36 // 10) = (6 << 4) | 3 = 0x63. No sign bit.
    assert encoded[6] == 0x63


from infrastructure.smsc.tpdu import decode_sms_submit, SmsSubmit


def test_decode_sms_submit_hello_to_111111():
    # SMS-SUBMIT TPDU for "Hello" to MSISDN 111111 (international, 7-bit GSM)
    # 01: TP-MTI=SMS-SUBMIT, RD=0, VPF=00, RP=0, UDHI=0, SRR=0
    # 00: TP-MR
    # 06 91 11 11 11: TP-DA (6 digits international)
    # 00: TP-PID
    # 00: TP-DCS (default 7-bit GSM)
    # 05: TP-UDL = 5 chars
    # C8329BFD06: TP-UD = "Hello" packed
    tpdu_hex = "01000691111111000005C8329BFD06"

    submit = decode_sms_submit(bytes.fromhex(tpdu_hex))

    assert submit.destination_msisdn == "111111"
    assert submit.dcs == 0x00
    assert submit.user_data == "Hello"


def test_decode_sms_submit_truncated_raises():
    import pytest
    with pytest.raises(ValueError):
        decode_sms_submit(b"\x01\x00")  # too short to even contain TP-DA length


from infrastructure.smsc.tpdu import encode_sms_deliver, decode_sms_deliver


def test_encode_sms_deliver_roundtrip_via_own_decoder():
    """Encode then decode using own decoder (we don't trust external libs for verification)."""
    timestamp = datetime(2026, 5, 4, 12, 34, 56, tzinfo=timezone.utc)
    encoded = encode_sms_deliver(
        originator_msisdn="222222",
        dcs=0x00,
        user_data="Hello",
        timestamp=timestamp,
    )

    deliver = decode_sms_deliver(encoded)

    assert deliver.originator_msisdn == "222222"
    assert deliver.dcs == 0x00
    assert deliver.user_data == "Hello"


def test_encode_sms_deliver_first_octet_mti():
    encoded = encode_sms_deliver(
        originator_msisdn="222222",
        dcs=0x00,
        user_data="X",
        timestamp=datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc),
    )
    # First octet: TP-MTI=00 (SMS-DELIVER), MMS=1 (no more messages waiting), low bits 0
    # Final value: bit pattern 0000_0100 = 0x04
    assert encoded[0] == 0x04


def test_decode_sms_deliver_truncated_raises():
    import pytest
    # 11 bytes: shortest input that exercises the new guard (4-byte was previously also rejected by old guard)
    with pytest.raises(ValueError):
        decode_sms_deliver(b"\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")


from infrastructure.smsc.tpdu import unwrap_rp_data, wrap_rp_data, RpDataDirection
from infrastructure.smsc.tpdu import swap_mo_to_mt


def test_swap_mo_to_mt():
    """End-to-end: MO RP-DATA hex → MT RP-DATA hex with OA=sender, recipient extracted."""
    mo_tpdu_hex = "01000691111111000005C8329BFD06"  # SUBMIT to 111111, "Hello"
    mo_rp_hex = "0000" + "00" + "04919999" + "41" + f"{len(bytes.fromhex(mo_tpdu_hex)):02x}" + mo_tpdu_hex

    recipient, mt_rp_bytes = swap_mo_to_mt(
        mo_rp_data=bytes.fromhex(mo_rp_hex),
        sender_msisdn="222222",
        smsc_msisdn="9999",
    )

    assert recipient == "111111"

    # Verify MT round-trip: unwrap RP-DATA, decode SMS-DELIVER with our own decoder
    direction, _ref, mt_tpdu = unwrap_rp_data(mt_rp_bytes)
    assert direction == RpDataDirection.NETWORK_TO_MS

    from infrastructure.smsc.tpdu import decode_sms_deliver
    deliver = decode_sms_deliver(mt_tpdu)
    assert deliver.originator_msisdn == "222222"
    assert deliver.user_data == "Hello"


def test_unwrap_rp_data_mo():
    # RP-DATA-MS-to-Network containing the SMS-SUBMIT TPDU
    tpdu_hex = "01000691111111000005C8329BFD06"
    # 00: MTI=0 (MS-to-Net), 00: ref
    # 00: RP-OA length=0
    # 04 91 99 99: RP-DA = 4 digits SMSC "9999" (international, BCD-swapped; 4 bytes)
    # 41 0F <tpdu>: RP-UD tag=0x41, len=0x0F (15 bytes), TPDU
    rp_hex = "0000" + "00" + "04919999" + "41" + f"{len(bytes.fromhex(tpdu_hex)):02x}" + tpdu_hex

    direction, ref, tpdu = unwrap_rp_data(bytes.fromhex(rp_hex))

    assert direction == RpDataDirection.MS_TO_NETWORK
    assert ref == 0x00
    assert tpdu.hex().upper() == tpdu_hex.upper()


def test_wrap_rp_data_mt_roundtrip():
    """wrap_rp_data(MT, ref, tpdu) → unwrap → original TPDU."""
    tpdu = bytes.fromhex("00" + "06" + "91222222" + "00000500A0E1F1B81C")  # arbitrary fake DELIVER

    wrapped = wrap_rp_data(RpDataDirection.NETWORK_TO_MS, ref=0x00, tpdu=tpdu, smsc_msisdn="9999")

    direction, ref, recovered = unwrap_rp_data(wrapped)
    assert direction == RpDataDirection.NETWORK_TO_MS
    assert recovered == tpdu


def test_unwrap_rp_data_truncated_raises():
    import pytest
    with pytest.raises(ValueError):
        unwrap_rp_data(b"\x00\x00\x00")  # only 3 bytes
