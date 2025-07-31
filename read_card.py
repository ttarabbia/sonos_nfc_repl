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


def read_ntag215(tag):
    """Read all available NTAG215 data."""
    print(f"\nTag Type: {getattr(tag, 'TYPE', 'Unknown')}")
    print(f"Tag Class: {tag.__class__.__name__}")
    print(f"UID: {hexlify(tag.identifier).decode()}")

    print(tag)
    
    # NTAG215 specifics
    if tag.__class__.__name__ == 'NTAG215':
        print("\nNTAG215 Details:")
        print("Memory Size: 504 bytes")
        print("User Memory: 144 bytes (address 0x04-0xAF)")
        print("Lock Bytes: 0xE0-0xE2")
        print("Capability Container: 0x03")
    
    # NDEF data
    if hasattr(tag, 'ndef') and tag.ndef:
        print("\nNDEF Info:")
        print(f"Formatted: {bool(tag.ndef)}")
        print(f"Writable: {tag.ndef.is_writeable}")
        print(f"Capacity: {getattr(tag.ndef, 'capacity', 'N/A')} bytes")

        dump = tag.dump()
        print(f"NDEF message raw: {tag.ndef}")
        try:
            print(f"NDEF records count: {len(tag.ndef.records)}")
        except Exception as e:
            print(f"Error reading records: {e}")
        print("\nNDEF Records:")
        for i, record in enumerate(tag.ndef.records):
            print(f"Record {i+1}: {record.type}")
            if hasattr(record, 'uri'):
                print(f"URI: {record.uri}")
            elif hasattr(record, 'text'):
                print(f"Text: {record.text}")
            else:
                print(f"Raw: {record}")

            # record = UriRecord(sys.argv[1])
            # # record = ndef.record('urn:nfc:wkt:U', sys.argv[1].encode('utf-8'))
            # # tag.ndef.message = [record]
            # tag.ndef.write_records([record])
            # print(f"successfully wrote URL: {sys.argv[1]} \n {record}")
            # print("Post-write check:")
            # print(f"  tag.ndef.message = {tag.ndef.message}")
            # print(f"  tag.ndef.records = {list(tag.ndef.records)}")
    else:
        print("\nNDEF: Not formatted")
        # Format the tag if not formatted
        try:
            print("Tag formatted successfully.")
            # Read the tag again after formatting
            if hasattr(tag, 'ndef') and tag.ndef:
                print("\nNDEF Info after formatting:")
                print(f"Formatted: {bool(tag.ndef)}")
                print(f"Writable: {tag.ndef.is_writeable}")
                print(f"Capacity: {getattr(tag.ndef, 'capacity', 'N/A')} bytes")
        except Exception as e:
            print(f"Failed to format tag: {e}")

def main():
    reset_reader()
    with nfc.ContactlessFrontend('usb') as clf:
        print("Waiting for NTAG215 tag...")
        clf.connect(rdwr={'on-connect': lambda tag: read_ntag215(tag) or True, 'on-release': lambda tag: None})
        clf.close()

if __name__ == "__main__":
    main()
