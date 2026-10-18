#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
import os
import pprint
import struct
import sys


class CoronaArchiver:
    _MAGIC_NUMBER_HEADER = b'\x72\x61\x63\x01'  # ASCII = rac\x01
    _MAGIC_NUMBER_INDEX = 1
    _MAGIC_NUMBER_DATA = 2
    _MAGIC_NUMBER_END = b'\xFF\xFF\xFF\xFF'

    def __init__(self):
        self.stream = None
        self.metadata = {}
        self.index = {}
        self.data = {}
        self._input_dir = ""
        self._output_dir = ""

    def __repr__(self):
        return pprint.pformat(vars(self))

    def pack(self, input_dir, output_file):
        self._input_dir = input_dir
        self.index.clear()

        with open(output_file, 'wb+') as f:
            self.stream = f

            files = [
                name for name in os.listdir(self._input_dir)
                if os.path.isfile(os.path.join(self._input_dir, name))
            ]
            self.metadata['length'] = len(files)

            # Write metadata header
            self.stream.write(self._MAGIC_NUMBER_HEADER)
            self.stream.write(struct.pack('<i', 1))  # revision
            self.stream.write(struct.pack('<i', 0))  # placeholder for data_offset_start
            self.stream.write(struct.pack('<i', self.metadata['length']))

            # Write index entries
            for filename in files:
                encoded_name = filename.encode('utf-8')
                padding_length = self._padding_length(len(encoded_name), 'index')
                offset = 1337  # placeholder offset
                self.stream.write(struct.pack('<iii', self._MAGIC_NUMBER_INDEX, offset, len(encoded_name)))
                self.stream.write(encoded_name)
                self._write_padding(padding_length)

            # Update data_offset_start
            tell = self.stream.tell()
            data_offset_start = tell - 12
            self.stream.seek(8, os.SEEK_SET)
            self.stream.write(struct.pack('<i', data_offset_start))
            self.stream.seek(tell, os.SEEK_SET)

            # Write data entries
            for filename in files:
                filepath = os.path.join(self._input_dir, filename)
                with open(filepath, 'rb') as f_append:
                    content = f_append.read()
                    length = len(content)
                    padding_length = self._padding_length(length, 'data')
                    nxt = length + 4 + padding_length

                    self.index[filename] = self.stream.tell()

                    self.stream.write(struct.pack('<iii', self._MAGIC_NUMBER_DATA, nxt, length))
                    self.stream.write(content)
                    self._write_padding(padding_length)

            # Write footer
            self.stream.write(self._MAGIC_NUMBER_END)
            self.stream.write(struct.pack('<i', 0))

            # Finalize index offsets
            self._write_finalize(files)

        print(f"File {output_file} successfully created.")

    def unpack(self, input_file, output_dir):
        self._output_dir = output_dir
        self.index.clear()

        os.makedirs(self._output_dir, exist_ok=True)

        with open(input_file, 'rb') as f:
            self.stream = f

            self.metadata['file_path'], self.metadata['file_name'] = os.path.split(input_file)
            self.metadata['file_size'] = os.path.getsize(input_file)
            self._read_metadata()

            # Read index entries
            for _ in range(self.metadata['length']):
                self._read_index_entry()

            # Read data entries using the offsets recorded in the index
            self._read_data_idx()

        print("Extraction done.")

    @staticmethod
    def _padding_length(length, entry_type):
        """
        Calculate padding to align to 4-byte boundaries:
          - index: always pads 1 to 4 null bytes (if multiple of 4, adds 4 bytes)
          - data: pads 0 to 3 null bytes (if multiple of 4, adds 0 bytes)
        """
        padding = 4 - (length % 4)
        if entry_type == 'data':
            return padding if padding < 4 else 0
        return padding

    def _write_finalize(self, files):
        self.stream.seek(16, os.SEEK_SET)
        self.stream.flush()

        for filename in files:
            encoded_len = len(filename.encode('utf-8'))
            padding_length = self._padding_length(encoded_len, 'index')

            self.stream.read(4)  # skip dtype
            self.stream.seek(self.stream.tell())
            self.stream.write(struct.pack('<i', self.index[filename]))
            self.stream.seek(self.stream.tell())

            length, = struct.unpack('<i', self.stream.read(4))
            self.stream.seek(length + padding_length, os.SEEK_CUR)

    def _write_padding(self, padding):
        self.stream.write(b'\x00' * padding)

    def _read_metadata(self):
        header = self.stream.read(4)
        self.metadata['header'] = header
        if header != self._MAGIC_NUMBER_HEADER:
            logging.error("Incorrect file type. Must be a *.car (Corona Archive) file type.")
            sys.exit(1)

        revision, = struct.unpack('<i', self.stream.read(4))
        self.metadata['revision'] = revision
        if revision != 1:
            logging.warning(
                f"This unpacker is intended for Corona Revision 1, it may not work on revision {revision}."
            )

        data_offset_start, = struct.unpack('<i', self.stream.read(4))
        self.metadata['data_offset_start'] = data_offset_start + self.stream.tell()

        length, = struct.unpack('<i', self.stream.read(4))
        self.metadata['length'] = length

    def _read_index_entry(self):
        dtype, offset, length = struct.unpack('<iii', self.stream.read(12))
        self.index[offset] = self.stream.read(length)
        logging.debug(f"{dtype} {offset} {length} {self.index[offset]}")

        padding_length = self._padding_length(length, 'index')
        self.stream.seek(padding_length, os.SEEK_CUR)

    def _read_data_entry(self, offset, filename):
        self.stream.seek(offset, os.SEEK_SET)
        dtype, nxt, length = struct.unpack('<iii', self.stream.read(12))
        content = self.stream.read(length)
        logging.debug(f"{dtype} {nxt} {length} {offset} {filename}")

        self._write_data_entry(content, offset, filename)

    def _read_data_idx(self):
        for offset, filename in self.index.items():
            self._read_data_entry(offset, filename)

    def _write_data_entry(self, content, offset, filename):
        if filename:
            new_filename = os.path.basename(filename.decode('utf-8', errors='replace'))
        else:
            new_filename = f"file-{offset}.extracted.lu"

        out_path = os.path.join(self._output_dir, new_filename)
        with open(out_path, "wb") as f:
            f.write(content)
            logging.info(f"File {new_filename} extracted to {self._output_dir}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format='%(asctime)s:%(levelname)-8s %(message)s')

    if len(sys.argv) != 4:
        print("Usage:")
        print("\tpacking:\tcorona-archiver.py -p 'input_dir' 'output_file'")
        print("\tunpacking:\tcorona-archiver.py -u 'input_file' 'output_dir'")
        sys.exit(1)

    method, input_path, output_path = sys.argv[1], sys.argv[2], sys.argv[3]
    archiver = CoronaArchiver()

    if method == '-p':
        archiver.pack(input_dir=input_path, output_file=output_path)
    elif method == '-u':
        archiver.unpack(input_file=input_path, output_dir=output_path)
    else:
        print("Invalid method. Use -p (pack) or -u (unpack).")
        sys.exit(1)
