import tempfile
from pathlib import Path
import struct
import unittest
from unittest.mock import patch
from check_driver import architecture, inspect

class DriverTests(unittest.TestCase):
    def test_elf_architecture_and_host_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'driver.so'
            for machine,name in [(183,'aarch64'),(62,'x86_64')]:
                h=bytearray(64);h[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',h,18,machine);path.write_bytes(h)
                self.assertEqual(architecture(path)['architecture'],name)
                with patch('check_driver.platform.system',return_value='Linux'),patch('check_driver.platform.machine',return_value='aarch64'):
                    self.assertEqual(inspect(path)['native_compatible'],machine==183)
                with patch('check_driver.platform.system',return_value='Darwin'):
                    self.assertFalse(inspect(path)['native_compatible'])
            path.write_bytes(b'not a driver')
            with self.assertRaises(ValueError):architecture(path)

if __name__=='__main__':unittest.main()
