#!/usr/bin/env python

import hashlib
import os
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime
from typing import IO, Any, Iterator, Set


SERVICES_URL = (
    "https://www.iana.org/assignments/service-names-port-numbers/service-names-port-numbers.xml"
)
SERVICES_XML = "service-names-port-numbers.xml"
SERVICES_FILE = "services"
SERVICES_HEADER = """# See also services(5) and IANA offical page :
# https://www.iana.org/assignments/service-names-port-numbers/service-names-port-numbers.xhtml
"""

PROTOCOLS_URL = (
    "https://www.iana.org/assignments/protocol-numbers/protocol-numbers.xml"
)
PROTOCOLS_XML = "protocol-numbers.xml"
PROTOCOLS_FILE = "protocols"
PROTOCOLS_HEADER = """# See also protocols(5) and IANA official page :
# https://www.iana.org/assignments/protocol-numbers/protocol-numbers.xhtml
"""


@contextmanager
def atomic_write(filename: str, mode: str = "w") -> Iterator[IO[Any]]:
    path = os.path.dirname(filename)
    try:
        file = tempfile.NamedTemporaryFile(
            delete=False,
            dir=path,
            mode=mode,
        )
        yield file
        file.flush()
        os.fsync(file.fileno())
        os.rename(file.name, filename)
    finally:
        try:
            os.remove(file.name)
        except OSError as e:
            if e.errno != 2:
                raise


def compute_sha256(fname: str) -> str:
    hash_sha256 = hashlib.sha256()
    with open(fname, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()


def parse_xml(source: str) -> ET.Element:
    it = ET.iterparse(open(source))
    for _, el in it:
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    root = it.root  # mypy: ignore
    return root


def parse_date(root_xml: ET.Element) -> datetime:
    updated = root_xml.find("updated")
    assert updated is not None and isinstance(updated.text, str)
    return datetime.strptime(updated.text, "%Y-%m-%d")


IGNORE_PATTERN = re.compile(
    r".*(unassigned|deprecated|reserved|historic).*",
    flags=re.IGNORECASE,
)


def has_spaces(s: str) -> bool:
    return re.match(r".*\s+.*", s) is not None


def write_services_file(source: str, destination: str) -> datetime:
    root = parse_xml(source)
    updated = parse_date(root)
    seen: Set[str] = set()

    with atomic_write(destination) as dst:
        dst.write(SERVICES_HEADER.format(updated.strftime("%Y-%m-%d")))

        for r in root.iter("record"):
            desc_el = r.find("description")
            desc = desc_el.text if desc_el is not None and desc_el.text else ""

            name_el = r.find("name")
            protocol_el = r.find("protocol")
            number_el = r.find("number")

            if (
                IGNORE_PATTERN.match(desc)
                or name_el is None
                or name_el.text is None
                or has_spaces(name_el.text)
                or protocol_el is None
                or protocol_el.text is None
                or number_el is None
                or number_el.text is None
            ):
                continue

            name = name_el.text.lower().replace("_", "-")
            protocol = protocol_el.text.lower()
            number = int(number_el.text.split("-")[0])

            assignments = f"{number}/{protocol}"
            entry = f"{name:<16} {assignments:<10}"

            if entry in seen:
                continue

            seen.add(entry)
            dst.write(entry)

            if desc and len(desc) < 70:
                dst.write(f" # {desc.replace('\n', '')}")

            dst.write("\n")

    return updated


def write_protocols_file(source: str, destination: str) -> datetime:
    root = parse_xml(source)
    updated = parse_date(root)

    with atomic_write(destination) as dst:
        dst.write(PROTOCOLS_HEADER.format(updated.strftime("%Y-%m-%d")))

        for r in root.iter("record"):
            desc_el = r.find("description")
            desc = desc_el.text if desc_el is not None and desc_el.text else ""

            name_el = r.find("name")
            value_el = r.find("value")

            if (
                IGNORE_PATTERN.match(desc)
                or name_el is None
                or name_el.text is None
                or IGNORE_PATTERN.match(name_el.text)
                or has_spaces(name_el.text)
                or value_el is None
                or value_el.text is None
            ):
                continue

            alias = name_el.text.split()[0]
            name = alias.lower()
            value = int(value_el.text)

            assignment = f"{value} {alias}"
            dst.write(f"{name:<16} {assignment:<16}")

            if desc and len(desc) < 70:
                dst.write(f" # {desc.replace('\n', '')}")

            dst.write("\n")

    return updated


def add_entry(tar: tarfile.TarFile, name: str, file: str) -> None:
    def reset(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo:
        tarinfo.uid = tarinfo.gid = 0
        tarinfo.uname = tarinfo.gname = "root"
        tarinfo.mtime = 0
        tarinfo.mode = 0o644
        return tarinfo

    tar.add(file, os.path.join(name, os.path.basename(file)), filter=reset)


def download(url: str, path: str) -> None:
    with atomic_write(path, "wb") as dst, urllib.request.urlopen(url) as src:
        shutil.copyfileobj(src, dst)


def main() -> None:
    if len(sys.argv) < 2:
        print(f"USAGE: {sys.argv[0]} download_path", file=sys.stderr)
        sys.exit(1)

    dest = sys.argv[1]
    os.makedirs(os.path.join(dest, "dist"), exist_ok=True)

    services_xml = os.path.join(dest, SERVICES_XML)
    protocols_xml = os.path.join(dest, PROTOCOLS_XML)

    try:
        download(SERVICES_URL, services_xml)
        download(PROTOCOLS_URL, protocols_xml)
    except OSError as e:
        print(
            f"Could not download iana service names and port numbers: {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    services_file = os.path.join(dest, "dist", SERVICES_FILE)
    services_xml_date = write_services_file(services_xml, services_file)

    protocols_file = os.path.join(dest, "dist", PROTOCOLS_FILE)
    protocols_xml_date = write_protocols_file(protocols_xml, protocols_file)

    version = max(services_xml_date, protocols_xml_date).strftime("%Y%m%d")

    name = f"iana-etc-{version}"
    tarball = os.path.join(dest, "dist", f"{name}.tar.gz")

    with tarfile.open(tarball, "w:gz") as tar:
        add_entry(tar, name, services_xml)
        add_entry(tar, name, services_file)
        add_entry(tar, name, protocols_xml)
        add_entry(tar, name, protocols_file)

    with atomic_write(
        os.path.join(dest, f"dist/iana-etc-{version}.tar.gz.sha256")
    ) as f:
        f.write(compute_sha256(tarball))

    with atomic_write(os.path.join(dest, ".version")) as f:
        f.write(version)


if __name__ == "__main__":
    main()
