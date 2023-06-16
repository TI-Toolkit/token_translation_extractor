import re
import sys
sys.path.insert(0, 'tivars_lib_py/')
from tivars.tokenizer import CE_BYTES, CE_TOKENS

def byte_mapping(n):
  """
  It is difficult to express my distaste that this function needs to exist at all

  implements the "token numbering" at https://wikiti.brandonw.net/index.php?title=Token_Hook
  """
  start_points = {
      0x00: 0x0000,
      0x5C: 0x0300,
      0x5D: 0x031E,
      0x5E: 0x0330,
      0x60: 0x038D,
      0x61: 0x03AB,
      0xAA: 0x03C9, # *why*
      0x62: 0x03E7,
      0x63: 0x049E,
      0x7E: 0x0549,
      0xBB: 0x0582,
      0xEF: 0x0864,
  }

  lower = n % 256
  upper = n // 256
  if upper not in start_points:
    raise "This token (%s) has a terrible disease; she must come with me immediately. Contact me if this error occurs." % hex(n)
  else:
    return start_points[upper] + 3 * lower

with open(input("Path to the _codeDataRelocated.img file as outputted by brandonw's Analyze8EX tool: "), "rb") as infile:
  file = infile.read()

  with open("extractor_output.txt", "w") as outfile:
    matches = re.search(b"\x21(...)\xb7\xed\x52\x38.\x21(...)", file, flags=re.DOTALL)
    section_length = int.from_bytes(matches[1], "little")
    starting_offset = int.from_bytes(matches[2], "little")

    for byte in CE_BYTES.keys(): # check out the logic documented in https://github.com/TI-Toolkit/awesome-ti-docs/blob/docs/how-espa%C3%B1ol-token-hook-works.svg and https://wikiti.brandonw.net/index.php?title=Token_Hook
      byte = int.from_bytes(byte, "big")
      de = byte_mapping(byte)
      if de > section_length:
        outfile.write(hex(byte) + " None\n")
      else:
        string_ref = int.from_bytes(file[de + starting_offset:de+starting_offset+3], "little")
        if string_ref == 0:
          outfile.write(hex(byte) + " None\n")
        else:
          length = file[string_ref]
          outfile.write(hex(byte) + " " + str(file[string_ref+1:string_ref+1+length]) + "\n") # first byte is the length of the string

print("Written to extractor_output.txt")