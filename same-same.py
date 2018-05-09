#!/usr/bin/env python3

import re
from sys import stdin, stderr, stdout
from typing import *


def main():

  buffer:List[Tuple[str, Match]] = []
  def flush_buffer():
    if buffer: handle_file_lines(buffer)
    buffer.clear()

  try:
    for rich_text in stdin:
      rich_text = rich_text.rstrip('\n')
      text = sgr_pat.sub('', rich_text) # remove colors.
      m = diff_pat.match(text)
      kind = 'unknown' if m is None else m.lastgroup
      assert kind is not None, (m, m.lastgroup, m.groupdict())
      if kind == 'diff': flush_buffer()
      buffer.append((kind, m, text, rich_text))
    flush_buffer()
  except BrokenPipeError:
    stderr.close() # this mollifies python and prevents warning message.


def handle_file_lines(lines):
  kind, match, text, rich_text = lines[0]
  skip = False
  if kind != 'diff': skip = True
  elif graph_pat.match(text).end(): skip = True

  if skip:
    for _, _, _, rich_text in lines: print(rich_text)
    return

  # First find whole lines that have moved.
  old_texts = {}
  new_texts = {}
  # Render lines.
  path = '<PATH>'
  for kind, match, text, rich_text in lines:
    if kind in dropped_kinds: continue
    elif kind == 'ctx':
      print(text[1:])
    elif kind == 'rem':
      print(TXT_R, text[1:], RST, sep='')
    elif kind == 'add':
      print(TXT_G, text[1:], RST, sep='')
    elif kind == 'loc':
      #old_pos = int(match['old_pos'])
      #old_end = old_pos + int(match['old_len'])
      new_pos = int(match['new_pos'])
      new_end = new_pos + int(match['new_len'])
      hunk_header = match['hunk_header']
      s = ' ' if hunk_header else ''
      #print()
      print(f'{BG_B}{path}:{new_pos}-{new_end}:{s}{hunk_header}{RST}')
    elif kind == 'diff':
      old_path = match['diff_a']
      new_path = match['diff_b']
      path = old_path if old_path == new_path else f'{old_path} -> {new_path}'
    elif kind in mode_kinds:
      print(f'{BG_B}{path}:{RST} {rich_text}')
    elif kind in pass_kinds:
      print(rich_text)
    else:
      print(f'{TXT_M}{kind.upper()}: {rich_text!r}')
      #raise Exception(f'unhandled kind: {kind}\n{text!r}')


dropped_kinds = {
  'idx', 'old', 'new'
}

mode_kinds = {
  'new_file',
  'del_file',
  'old_mode',
  'new_mode',
}

pass_kinds = {
  'empty',
  'unknown',
}


sgr_pat = re.compile(r'\x1B\[[0-9;]*m')

graph_pat = re.compile(r'(?x) [\ \* \| \\ /]*')

diff_re = r'''(?x)
  (?:
    (?P<empty> $)
  | (?P<commit>   commit\ [0-9a-z]{40})
  | (?P<author>   Author:       )
  | (?P<date>     Date:         )
  | (?P<diff>     diff\ --git\ a/(?P<diff_a>.+)\ b/(?P<diff_b>.+) )
  | (?P<idx>      index         )
  | (?P<old>      ---           )
  | (?P<new>      \+\+\+        )
  | (?P<loc>      @@\ -(?P<old_pos>\d+),(?P<old_len>\d+)\ \+(?P<new_pos>\d+),(?P<new_len>\d+)\ @@\ ?(?P<hunk_header>.*) )
  | (?P<ctx>      \  (?P<ctx_text>.*) )
  | (?P<rem>      -  (?P<rem_text>.*) )
  | (?P<add>      \+ (?P<add_text>.*) )
  | (?P<new_file> new\ file\ mode )
  | (?P<del_file> deleted\ file\ mode )
  | (?P<old_mode> old\ mode )
  | (?P<new_mode> new\ mode )
  )
'''

diff_pat = re.compile(diff_re)


# ANSI control sequence indicator.
CSI = '\x1B['

def sgr(*codes:Any) -> str:
  'Select Graphic Rendition control sequence string.'
  code = ';'.join(str(c) for c in codes)
  return f'{CSI}{code}m'

RST = sgr()

RST_BOLD, RST_ULINE, RST_BLINK, RST_INVERT, RST_TXT, RST_BG = (sgr(i) for i in (22, 24, 25, 27, 39, 49))

BOLD, ULINE, BLINK, INVERT = (sgr(i) for i in (1, 4, 5, 7))

# color text: dark gray, red, green, yellow, blue, magenta, cyan, light gray.
ansi_txt_primary_indices = range(30, 38)
ansi_txt_primaries = tuple(sgr(i) for i in ansi_txt_primary_indices)
TXT_D, TXT_R, TXT_G, TXT_Y, TXT_B, TXT_M, TXT_C, TXT_L = ansi_txt_primaries

# color background: dark gray, red, green, yellow, blue, magenta, cyan, light gray.
ansi_bg_primary_indices = range(40, 48)
ansi_bg_primaries = tuple(sgr(i) for i in ansi_bg_primary_indices)
BG_D, BG_R, BG_G, BG_Y, BG_B, BG_M, BG_C, BG_L = ansi_bg_primaries


if __name__ == '__main__': main()
