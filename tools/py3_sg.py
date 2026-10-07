"""Minimal pure-Python stand-in for the py3_sg C extension (SG_IO via ctypes).

Only the two calls used by jms_settings.py are provided:
    read_as_bin_str(fileobj, cdb, length, timeout_ms) -> bytes
    write(fileobj, cdb, data, timeout_ms=3000) -> None
Both raise OSError / RuntimeError on a SCSI error, as py3_sg does.
"""
import ctypes
import fcntl

SG_IO = 0x2285
SG_DXFER_NONE = -1
SG_DXFER_TO_DEV = -2
SG_DXFER_FROM_DEV = -3


class _SgIoHdr(ctypes.Structure):
    _fields_ = [
        ("interface_id", ctypes.c_int),
        ("dxfer_direction", ctypes.c_int),
        ("cmd_len", ctypes.c_ubyte),
        ("mx_sb_len", ctypes.c_ubyte),
        ("iovec_count", ctypes.c_ushort),
        ("dxfer_len", ctypes.c_uint),
        ("dxferp", ctypes.c_void_p),
        ("cmdp", ctypes.c_void_p),
        ("sbp", ctypes.c_void_p),
        ("timeout", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("pack_id", ctypes.c_int),
        ("usr_ptr", ctypes.c_void_p),
        ("status", ctypes.c_ubyte),
        ("masked_status", ctypes.c_ubyte),
        ("msg_status", ctypes.c_ubyte),
        ("sb_len_wr", ctypes.c_ubyte),
        ("host_status", ctypes.c_ushort),
        ("driver_status", ctypes.c_ushort),
        ("resid", ctypes.c_int),
        ("duration", ctypes.c_uint),
        ("info", ctypes.c_uint),
    ]


def _sg_io(fileobj, cdb, direction, buf, timeout_ms):
    cdb_buf = ctypes.create_string_buffer(bytes(cdb), len(cdb))
    sense = ctypes.create_string_buffer(32)
    hdr = _SgIoHdr()
    hdr.interface_id = ord("S")
    hdr.dxfer_direction = direction
    hdr.cmd_len = len(cdb)
    hdr.mx_sb_len = 32
    hdr.dxfer_len = len(buf) if buf is not None else 0
    hdr.dxferp = ctypes.cast(buf, ctypes.c_void_p) if buf is not None else None
    hdr.cmdp = ctypes.cast(cdb_buf, ctypes.c_void_p)
    hdr.sbp = ctypes.cast(sense, ctypes.c_void_p)
    hdr.timeout = int(timeout_ms)
    fcntl.ioctl(fileobj.fileno(), SG_IO, hdr)  # raises OSError on ioctl failure
    if hdr.status or hdr.host_status or hdr.driver_status:
        raise RuntimeError(
            "SCSI error: status=0x%02x host=0x%04x driver=0x%04x sense=%s"
            % (hdr.status, hdr.host_status, hdr.driver_status,
               sense.raw[:hdr.sb_len_wr].hex()))
    return hdr


def read_as_bin_str(fileobj, cdb, length, timeout_ms=3000):
    buf = ctypes.create_string_buffer(length)
    hdr = _sg_io(fileobj, cdb, SG_DXFER_FROM_DEV, buf, timeout_ms)
    got = length - hdr.resid if hdr.resid >= 0 else length
    return buf.raw[:got]


def write(fileobj, cdb, data, timeout_ms=3000):
    data = bytes(data)
    if data:
        buf = ctypes.create_string_buffer(data, len(data))
        _sg_io(fileobj, cdb, SG_DXFER_TO_DEV, buf, timeout_ms)
    else:
        _sg_io(fileobj, cdb, SG_DXFER_NONE, None, timeout_ms)
