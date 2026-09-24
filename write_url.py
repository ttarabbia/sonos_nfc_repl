import nfc
import ndef
from ndef.uri import UriRecord
from binascii import hexlify
import usb.core
import sys

def reset_reader():
    """Run the reader setup sequence used by this utility previously."""
    dev = usb.core.find(idVendor=0x072f, idProduct=0x2200)
    if dev is None:
        raise ValueError("Device not found")

    try:
        dev.detach_kernel_driver(0)
        print("Detached kernel driver.")
    except Exception as error:
        print(f"Couldn't detach: {error}")

    try:
        dev.set_configuration()
        print("Device configured.")
    except Exception as error:
        print(f"Failed to configure: {error}")

def write_url(tag):
    if len(sys.argv) == 2:
        url = sys.argv[1]
        record = UriRecord(url)
        tag.ndef.records = [record]
        print(f"✅ Successfully wrote URL: {url}")


def main():
    reset_reader()
    with nfc.ContactlessFrontend('usb') as clf:
        print("Waiting for NTAG215 tag...")
        clf.connect(rdwr={'on-connect': lambda tag: write_url(tag) or True, 'on-release': lambda tag: None})


if __name__ == "__main__":
    main()
