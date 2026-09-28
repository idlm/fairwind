"""只读 DNS 诊断：输出确定性的 rcode 与记录数归类，供 dns_evidence.sh 复现证据。"""

import argparse
import random
import socket
import struct
import sys

QUERY_TYPES = {"A": 1, "NS": 2, "CNAME": 5, "SOA": 6, "AAAA": 28}
TYPE_NAMES = {value: name for name, value in QUERY_TYPES.items()}
RCODE_NAMES = {0: "NOERROR", 1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN", 4: "NOTIMP", 5: "REFUSED"}
EAI_NAMES = {getattr(socket, name): name for name in dir(socket) if name.startswith("EAI_")}
HEADER = struct.Struct(">HHHHHH")
DNS_PORT = 53
TIMEOUT_SECONDS = 5.0
MAX_HOPS = 8
TYPE_A = 1
TYPE_NS = 2
TYPE_AAAA = 28
ADDRESS_LENGTH = {TYPE_A: 4, TYPE_AAAA: 16}


def encode_name(name: str) -> bytes:
    labels = [label for label in name.split(".") if label]
    if not labels or any(len(label) > 63 for label in labels):
        raise ValueError("invalid name")
    encoded = b"".join(bytes([len(label)]) + label.encode("ascii") for label in labels)
    return encoded + b"\x00"


def read_name(data: bytes, position: int) -> tuple[str, int]:
    labels: list[str] = []
    end: int | None = None
    hops = 0
    while True:
        if position >= len(data):
            raise ValueError("truncated name")
        length = data[position]
        if length == 0:
            position += 1
            break
        if length & 0xC0 == 0xC0:
            if position + 1 >= len(data) or hops >= MAX_HOPS:
                raise ValueError("bad pointer")
            pointer = struct.unpack(">H", data[position : position + 2])[0] & 0x3FFF
            if end is None:
                end = position + 2
            position = pointer
            hops += 1
            continue
        labels.append(data[position + 1 : position + 1 + length].decode("ascii", "replace"))
        position += 1 + length
    return ".".join(labels), end if end is not None else position


def read_record(data: bytes, position: int) -> tuple[dict, int]:
    owner, position = read_name(data, position)
    rtype, _, _, length = struct.unpack(">HHIH", data[position : position + 10])
    position += 10
    raw = data[position : position + length]
    value: str | None = None
    if len(raw) == ADDRESS_LENGTH.get(rtype):
        family = socket.AF_INET if rtype == TYPE_A else socket.AF_INET6
        value = socket.inet_ntop(family, raw)
    elif rtype == TYPE_NS:
        value, _ = read_name(data, position)
    record = {"type": TYPE_NAMES.get(rtype, str(rtype)), "value": value, "owner": owner}
    return record, position + length


def parse_response(data: bytes) -> dict:
    if len(data) < HEADER.size:
        raise ValueError("short response")
    _, flags, questions, answers, authority, additional = HEADER.unpack(data[: HEADER.size])
    position = HEADER.size
    for _ in range(questions):
        _, position = read_name(data, position)
        position += 4
    records = []
    for _ in range(answers):
        record, position = read_record(data, position)
        records.append(record)
    return {
        "rcode": RCODE_NAMES.get(flags & 0x000F, f"RCODE_{flags & 0x000F}"),
        "answers": answers,
        "records": records,
        "authority": authority,
        "additional": additional,
    }


def ask(name: str, qtype: int, server: str, timeout: float = TIMEOUT_SECONDS) -> dict:
    packet = HEADER.pack(random.randrange(65536), 0x0100, 1, 0, 0, 0)
    packet += encode_name(name) + struct.pack(">HH", qtype, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(packet, (server, DNS_PORT))
        data, _ = sock.recvfrom(4096)
    return parse_response(data)


def verdict(result: dict) -> str:
    if result["rcode"] == "NXDOMAIN":
        return "NXDOMAIN"
    if result["rcode"] != "NOERROR":
        return "FAILED_" + result["rcode"]
    return "ANSWER" if result["answers"] else "NOERROR_NODATA"


def command_resolve(args: argparse.Namespace) -> int:
    prefix = f"resolve host={args.host} server={args.server} type={args.type}"
    try:
        result = ask(args.host, QUERY_TYPES[args.type], args.server)
    except (OSError, ValueError) as error:
        print(f"{prefix} verdict=QUERY_FAILED error={error}")
        return 1
    values = ",".join(str(one["value"]) for one in result["records"] if one["value"]) or "-"
    print(
        f"{prefix} rcode={result['rcode']} answers={result['answers']} "
        f"verdict={verdict(result)} values={values}"
    )
    return 0


def command_ns(args: argparse.Namespace) -> int:
    names: list[str] = []
    for server in (args.server, "8.8.8.8"):
        try:
            result = ask(args.host, TYPE_NS, server)
        except (OSError, ValueError):
            continue
        names = [str(one["value"]) for one in result["records"] if one["value"]]
        if names:
            break
    if not names:
        print(f"ns host={args.host} verdict=UNKNOWN")
        return 1
    for name in names:
        print(f"ns={name}")
    return 0


def command_system(args: argparse.Namespace) -> int:
    try:
        infos = socket.getaddrinfo(args.host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        code = error.errno
        name = EAI_NAMES.get(code, "EAI_UNKNOWN") if code is not None else "EAI_UNKNOWN"
        print(f"system host={args.host} verdict=FAILED code={code} name={name}")
        return 1
    addresses = sorted({info[4][0] for info in infos})
    print(
        f"system host={args.host} verdict=ANSWER count={len(addresses)} "
        f"values={','.join(addresses)}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="dns_query", description="只读 DNS 诊断")
    commands = parser.add_subparsers(dest="command", required=True)
    resolve = commands.add_parser("resolve", help="对指定服务器查询记录")
    resolve.add_argument("host")
    resolve.add_argument("--type", choices=sorted(QUERY_TYPES), default="A")
    resolve.add_argument("--server", default="1.1.1.1")
    ns = commands.add_parser("ns", help="列出该域名的权威 NS")
    ns.add_argument("host")
    ns.add_argument("--server", default="1.1.1.1")
    system = commands.add_parser("system", help="用系统解析器解析")
    system.add_argument("host")
    args = parser.parse_args()
    if args.command == "resolve":
        return command_resolve(args)
    if args.command == "ns":
        return command_ns(args)
    return command_system(args)


if __name__ == "__main__":
    sys.exit(main())
