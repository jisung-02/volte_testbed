"""TPDU codec for SMS-SUBMIT / SMS-DELIVER / RP-DATA per 3GPP TS 23.040 + TS 24.011.

Implements only what an IMS SMSC needs: decode MO SMS-SUBMIT, encode MT SMS-DELIVER,
RP-DATA wrap/unwrap, and a swap helper. All in-house — see plan header for library
rationale (smspdu is Py2-only, smsutil only handles charset)."""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
import smsutil


# ---------- Address codec (TP-DA / TP-OA / RP-OA / RP-DA shape) ----------

def encode_address(msisdn: str, type_of_address: int = 0x91) -> bytes:
    """Encode an MSISDN as length-prefixed BCD with type-of-address byte.

    Length byte = number of digits (not bytes). Default ToA 0x91 = international.
    Returns: length || type || BCD-swapped digits (with F nibble pad if odd)."""
    if not msisdn:
        return b"\x00"  # zero-length address
    digits = msisdn
    if len(digits) % 2:
        digits += "F"
    bcd = bytes(int(digits[i + 1] + digits[i], 16) for i in range(0, len(digits), 2))
    return bytes([len(msisdn), type_of_address]) + bcd


def decode_address(buf: bytes, offset: int) -> tuple[str, int]:
    """Decode an address. Returns (msisdn, bytes_consumed)."""
    length = buf[offset]
    if length == 0:
        return "", 1
    bcd_len = (length + 1) // 2
    bcd = buf[offset + 2 : offset + 2 + bcd_len]
    digits = "".join(f"{b & 0x0F}{b >> 4}" for b in bcd)
    return digits[:length], 2 + bcd_len


# ---------- 7-bit GSM septet packing (TP-UD body for DCS=0x00) ----------

def pack_septets(septets: bytes) -> bytes:
    """Pack 7-bit septets into octets per 3GPP TS 23.038. Each input byte = one septet (low 7 bits)."""
    out = bytearray()
    bit_buf = 0
    bit_count = 0
    for s in septets:
        bit_buf |= (s & 0x7F) << bit_count
        bit_count += 7
        while bit_count >= 8:
            out.append(bit_buf & 0xFF)
            bit_buf >>= 8
            bit_count -= 8
    if bit_count > 0:
        out.append(bit_buf & 0xFF)
    return bytes(out)


def unpack_septets(packed: bytes, num_septets: int) -> bytes:
    """Inverse of pack_septets. num_septets = TP-UDL value."""
    out = bytearray()
    bit_buf = 0
    bit_count = 0
    pos = 0
    while len(out) < num_septets:
        if bit_count < 7 and pos < len(packed):
            bit_buf |= packed[pos] << bit_count
            bit_count += 8
            pos += 1
        out.append(bit_buf & 0x7F)
        bit_buf >>= 7
        bit_count -= 7
    return bytes(out)


# ---------- SCTS (Service Center Time Stamp) ----------

def encode_scts(ts: datetime) -> bytes:
    """Encode timestamp as 7-byte semi-octet BCD per 3GPP TS 23.040 §9.2.3.11.

    Format: YY MM DD HH MM SS TZ (each pair nibble-swapped). TZ in 15-min units.
    The TZ byte is BCD-encoded first, then bit 3 of the low nibble carries the sign."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    utc_offset_min = int(ts.utcoffset().total_seconds() // 60) if ts.utcoffset() else 0
    tz_quarters = abs(utc_offset_min) // 15
    # BCD-encode tz_quarters: units digit → high nibble, tens digit → low nibble
    tz_wire = ((tz_quarters % 10) << 4) | (tz_quarters // 10)
    if utc_offset_min < 0:
        tz_wire |= 0x08  # bit 3 of low nibble (tens digit position) = sign
    fields = [ts.year % 100, ts.month, ts.day, ts.hour, ts.minute, ts.second]
    return bytes(((f % 10) << 4) | (f // 10) for f in fields) + bytes([tz_wire])


# ---------- SMS-SUBMIT decoder ----------

@dataclass
class SmsSubmit:
    destination_msisdn: str
    pid: int
    dcs: int
    user_data: str
    message_reference: int


def decode_sms_submit(tpdu: bytes) -> SmsSubmit:
    """Decode a SMS-SUBMIT TPDU per 3GPP TS 23.040 §9.2.2.2.

    Supports default 7-bit GSM coding (DCS=0x00). Other DCS values raise NotImplementedError."""
    if len(tpdu) < 7:  # first_octet + MR + min DA(3) + PID + DCS + UDL
        raise ValueError(f"SMS-SUBMIT too short: {len(tpdu)} bytes")
    first_octet = tpdu[0]
    vpf = (first_octet >> 3) & 0x03  # validity period format
    mr = tpdu[1]
    da, da_consumed = decode_address(tpdu, 2)
    pos = 2 + da_consumed
    pid = tpdu[pos]
    dcs = tpdu[pos + 1]
    pos += 2
    # Skip TP-VP per VPF
    if vpf == 0:
        pass  # no VP
    elif vpf == 2:
        pos += 1  # relative
    elif vpf in (1, 3):
        pos += 7  # enhanced or absolute
    udl = tpdu[pos]
    ud_packed = tpdu[pos + 1:]
    if dcs == 0x00:
        septets = unpack_septets(ud_packed, num_septets=udl)
        user_data = smsutil.decode(bytes(septets))
    else:
        raise NotImplementedError(f"DCS 0x{dcs:02x} not implemented (only default GSM 7-bit)")
    return SmsSubmit(
        destination_msisdn=da,
        pid=pid,
        dcs=dcs,
        user_data=user_data,
        message_reference=mr,
    )


# ---------- SMS-DELIVER encoder + decoder ----------

@dataclass
class SmsDeliver:
    originator_msisdn: str
    pid: int
    dcs: int
    user_data: str
    timestamp: datetime


def encode_sms_deliver(
    originator_msisdn: str,
    dcs: int,
    user_data: str,
    timestamp: datetime | None = None,
) -> bytes:
    """Encode a SMS-DELIVER TPDU per 3GPP TS 23.040 §9.2.2.1.

    Sets MMS=1 (no more messages waiting). Supports DCS=0x00 (default 7-bit GSM)."""
    if dcs != 0x00:
        raise NotImplementedError(f"DCS 0x{dcs:02x} not implemented (only default GSM 7-bit)")
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)

    # First octet: TP-MTI=00 (DELIVER), MMS=1 (bit 2 set), all other bits zero
    first_octet = 0x04
    oa = encode_address(originator_msisdn)
    pid = 0x00
    scts = encode_scts(timestamp)

    septets = smsutil.encode(user_data)  # text → GSM 03.38 septets (1 byte per char)
    udl = len(septets)
    ud_packed = pack_septets(septets)

    return bytes([first_octet]) + oa + bytes([pid, dcs]) + scts + bytes([udl]) + ud_packed


def decode_sms_deliver(tpdu: bytes) -> SmsDeliver:
    """Decode a SMS-DELIVER TPDU. Inverse of encode_sms_deliver."""
    if len(tpdu) < 12:  # first_octet + OA-zero(1) + PID + DCS + SCTS(7) + UDL
        raise ValueError(f"SMS-DELIVER too short: {len(tpdu)} bytes")
    pos = 1  # skip first octet
    oa, oa_consumed = decode_address(tpdu, pos)
    pos += oa_consumed
    pid = tpdu[pos]
    dcs = tpdu[pos + 1]
    pos += 2
    scts_bytes = tpdu[pos : pos + 7]
    pos += 7
    timestamp = _decode_scts(scts_bytes)
    udl = tpdu[pos]
    ud_packed = tpdu[pos + 1 :]
    if dcs == 0x00:
        septets = unpack_septets(ud_packed, num_septets=udl)
        user_data = smsutil.decode(bytes(septets))
    else:
        raise NotImplementedError(f"DCS 0x{dcs:02x} not implemented")
    return SmsDeliver(
        originator_msisdn=oa,
        pid=pid,
        dcs=dcs,
        user_data=user_data,
        timestamp=timestamp,
    )


def _decode_scts(buf: bytes) -> datetime:
    """Decode SCTS semi-octet BCD bytes to datetime."""
    def swap(b: int) -> int:
        return (b >> 4) | ((b & 0x0F) << 4)
    fields = [swap(b) for b in buf[:6]]
    # Each field is now BCD: tens digit in high nibble, units in low. Convert.
    nums = [(f >> 4) * 10 + (f & 0x0F) for f in fields]
    yy, mm, dd, hh, mi, ss = nums
    year = 2000 + yy if yy < 70 else 1900 + yy
    return datetime(year, mm, dd, hh, mi, ss, tzinfo=timezone.utc)


# ---------- RP-DATA layer (3GPP TS 24.011) ----------

class RpDataDirection(IntEnum):
    MS_TO_NETWORK = 0
    NETWORK_TO_MS = 1


def unwrap_rp_data(rp: bytes) -> tuple[RpDataDirection, int, bytes]:
    """Parse RP-DATA per 3GPP TS 24.011. Returns (direction, message_reference, tpdu_bytes)."""
    if len(rp) < 6:  # mti+ref(2) + min OA(1, length=0) + min DA(1, length=0) + tag(1) + UDL(1)
        raise ValueError(f"RP-DATA too short: {len(rp)} bytes")
    direction = RpDataDirection(rp[0] & 0x07)
    ref = rp[1]
    pos = 2
    _oa, consumed = decode_address(rp, pos)
    pos += consumed
    _da, consumed = decode_address(rp, pos)
    pos += consumed
    tag = rp[pos]
    if tag != 0x41:
        raise ValueError(f"unexpected RP-UD tag: 0x{tag:02x}")
    udl = rp[pos + 1]
    tpdu = rp[pos + 2 : pos + 2 + udl]
    return direction, ref, tpdu


def wrap_rp_data(direction: RpDataDirection, ref: int, tpdu: bytes, smsc_msisdn: str) -> bytes:
    """Build RP-DATA. For MS→Net direction, RP-DA=smsc_msisdn, RP-OA empty.
    For Net→MS direction, RP-OA=smsc_msisdn, RP-DA empty."""
    header = bytes([int(direction) & 0x07, ref & 0xFF])
    if direction == RpDataDirection.MS_TO_NETWORK:
        oa = b"\x00"
        da = encode_address(smsc_msisdn)
    else:
        oa = encode_address(smsc_msisdn)
        da = b"\x00"
    ud = bytes([0x41, len(tpdu)]) + tpdu
    return header + oa + da + ud


# ---------- MO→MT swap helper ----------

def swap_mo_to_mt(mo_rp_data: bytes, sender_msisdn: str, smsc_msisdn: str) -> tuple[str, bytes]:
    """Decode MO RP-DATA + SMS-SUBMIT, build MT RP-DATA + SMS-DELIVER.

    Returns (recipient_msisdn, mt_rp_data_bytes)."""
    direction, ref, mo_tpdu = unwrap_rp_data(mo_rp_data)
    if direction != RpDataDirection.MS_TO_NETWORK:
        raise ValueError(f"expected MS_TO_NETWORK, got {direction}")
    submit = decode_sms_submit(mo_tpdu)
    mt_tpdu = encode_sms_deliver(
        originator_msisdn=sender_msisdn,
        dcs=submit.dcs,
        user_data=submit.user_data,
    )
    mt_rp = wrap_rp_data(RpDataDirection.NETWORK_TO_MS, ref=ref, tpdu=mt_tpdu, smsc_msisdn=smsc_msisdn)
    return submit.destination_msisdn, mt_rp
