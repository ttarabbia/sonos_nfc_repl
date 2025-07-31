import nfc
import ndef
from ndef.uri import UriRecord
from binascii import hexlify
import usb.core
import sys


def reset_reader():
    """Reset the NFC reader USB connection."""
    dev = usb.core.find(idVendor=0x072f, idProduct=0x2200)
    if dev is None:
        raise ValueError("Device not found")
    
    try:
        dev.detach_kernel_driver(0)
        print("Detached kernel driver.")
    except Exception as e:
        print(f"Couldn't detach: {e}")
    
    try:
        dev.set_configuration()
        print("Device claimed!")
    except Exception as e:
        print(f"Failed to claim: {e}")

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
        clf.close()


if __name__ == "__main__":
    main()
