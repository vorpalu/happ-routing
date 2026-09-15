"""Read/write Xray GeoSite/GeoIP protobuf messages without extra packages.

Schema: XTLS/Xray-core common/geodata/geodat.proto (formerly app/router/config.proto).
"""
from pathlib import Path


def varint(data, offset):
    value = shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
        shift += 7
        if shift > 63:
            raise ValueError('Oversized protobuf varint')
    raise ValueError('Truncated protobuf varint')


def fields(data):
    offset = 0
    while offset < len(data):
        tag, offset = varint(data, offset)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            value, offset = varint(data, offset)
        elif wire == 2:
            length, offset = varint(data, offset)
            end = offset + length
            if end > len(data):
                raise ValueError('Truncated protobuf field')
            value = data[offset:end]
            offset = end
        elif wire in (1, 5):
            end = offset + (8 if wire == 1 else 4)
            value, offset = data[offset:end], end
        else:
            raise ValueError(f'Unsupported wire type {wire}')
        yield field, wire, value


def encode_varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result + bytes([value]))


def blob(field, value):
    return encode_varint(field << 3 | 2) + encode_varint(len(value)) + value


def entries(path):
    result = {}
    for field, wire, data in fields(Path(path).read_bytes()):
        if field != 1 or wire != 2:
            raise ValueError('Unexpected top-level geodata field')
        code = next(value.decode() for field, wire, value in fields(data) if field == 1)
        result[code.lower()] = data
    return result


def domains(entry):
    result = []
    for field, wire, data in fields(entry):
        if field != 2:
            continue
        parts = {field: value for field, wire, value in fields(data) if field in (1, 2)}
        result.append((parts.get(1, 0), parts[2].decode()))
    return result


def encode_domains(code, rules):
    data = blob(1, code.upper().encode())
    for kind, value in rules:
        domain = encode_varint(8) + encode_varint(kind) + blob(2, value.encode())
        data += blob(2, domain)
    return data


def write_entries(path, values):
    Path(path).write_bytes(b''.join(blob(1, value) for value in values))


def deduplicate(rules):
    rules = set(rules)
    suffixes = {value for kind, value in rules if kind == 2}
    result = []
    for kind, value in sorted(rules):
        if kind in (2, 3):
            labels = value.split('.')
            covered = any('.'.join(labels[i:]) in suffixes for i in range(1, len(labels)))
            if covered or (kind == 3 and value in suffixes):
                continue
        result.append((kind, value))
    return result
