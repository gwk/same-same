#!/usr/bin/env python3

import re
from argparse import ArgumentParser
from os import environ
from sys import stderr, stdout
from typing import *
from typing import Match
from dataclasses import dataclass # type: ignore


@dataclass
class DiffLine:
  kind: str
  match: Match
  rich_text: str
  old_num: int = -1
  new_num: int = -1
  text: str = ''

  @property
  def plain_text(self) -> str:
    return self.match.string # type: ignore


def main() -> None:

  arg_parser = ArgumentParser(prog='same-same', description='Git diff filter.')
  arg_parser.add_argument('-interactive', action='store_true', help="Accommodate git's interactive mode.")
  args = arg_parser.parse_args()

  # Git can generate utf8-illegal sequences; ignore them.
  stdin = open(0, errors='replace')

  if 'SAME_SAME_OFF' in environ:
    for line in stdin:
      stdout.write(line)
    exit(0)

  dbg = ('SAME_SAME_DBG' in environ)

  buffer:List[DiffLine] = []
  path = '<PATH>'

  def flush_buffer() -> None:
    nonlocal buffer
    if buffer:
      handle_file_lines(buffer, path=path, interactive=args.interactive)
      buffer = []

  try:
    for rich_text in stdin:
      rich_text = rich_text.rstrip('\n')
      plain_text = sgr_pat.sub('', rich_text) # remove colors.
      match = diff_pat.match(plain_text)
      assert match is not None
      kind = match.lastgroup
      assert kind is not None, match
      if dbg:
        print(kind, ':', repr(plain_text))
        continue
      if kind == 'diff':
        flush_buffer()
        path = match['diff_b']
        if '/' not in path:
          path = './' + path
        assert path is not None
      buffer.append(DiffLine(kind, match, rich_text)) # type: ignore
    flush_buffer()
  except BrokenPipeError:
    stderr.close() # Prevents warning message.


def handle_file_lines(lines:List[DiffLine], path:str, interactive:bool) -> None:
  first = lines[0]
  kind = first.kind
  skip = False
  if kind not in ('diff', 'loc'): skip = True
  elif graph_pat.match(first.plain_text).end(): skip = True # type: ignore

  if skip:
    for line in lines: print(line.rich_text)
    return

  old_ctx_nums:Set[int] = set() # Line numbers of context lines.
  new_ctx_nums:Set[int] = set() # ".
  old_lines:Dict[int, DiffLine] = {} # Maps of line numbers to line structs.
  new_lines:Dict[int, DiffLine] = {} # ".
  old_uniques:Dict[str, Optional[int]] = {} # Maps unique line bodies to line numbers.
  new_uniques:Dict[str, Optional[int]] = {} # ".
  #moved_pairs = {} # new to old indices.
  old_num = 0 # 1-indexed source line number.
  new_num = 0 # ".
  chunk_idx = 0

  # Accumulate source lines into structures.
  for line in lines:
    match = line.match
    kind = line.kind
    if kind == 'ctx':
      line.text = match['ctx_text']
    elif kind == 'rem':
      line.text = match['rem_text']
      insert_unique_line(old_uniques, line.text, old_num)
    elif kind == 'add':
      line.text = match['add_text']
      insert_unique_line(new_uniques, line.text, new_num)
    elif kind == 'loc':
      o = int(match['old_num'])
      if o > 0:
        assert o > old_num, (o, old_num, match.string)
        old_num = o
      n = int(match['new_num'])
      if n > 0:
        assert n > new_num
        new_num = n
      continue
    if kind in ('ctx', 'rem'):
      assert old_num not in old_lines
      assert old_num not in old_ctx_nums
      line.old_num = old_num
      old_lines[old_num] = line
      old_ctx_nums.add(old_num)
      old_num += 1
    if kind in ('ctx', 'add'):
      assert new_num not in new_lines
      assert new_num not in new_ctx_nums
      line.new_num = new_num
      new_lines[new_num] = line
      new_ctx_nums.add(new_num)
      new_num += 1

  # Detect moved lines.

  def diff_lines_match(old_idx:int, new_idx:int) -> bool:
    if old_idx in old_ctx_nums or new_idx in new_ctx_nums: return False
    try: return old_lines[old_idx].text.strip() == new_lines[new_idx].text.strip()
    except KeyError: return False

  old_moved_nums:Set[int] = set()
  new_moved_nums:Set[int] = set()
  for body, new_idx in new_uniques.items():
    if new_idx is None: continue
    old_idx = old_uniques.get(body)
    if old_idx is None: continue
    p_o = old_idx
    p_n = new_idx
    while diff_lines_match(p_o-1, p_n-1):
      p_o -= 1
      p_n -= 1
    e_o = old_idx + 1
    e_n = new_idx + 1
    while diff_lines_match(e_o, e_n):
      e_o += 1
      e_n += 1
    old_moved_nums.update(range(p_o, e_o))
    new_moved_nums.update(range(p_n, e_n))

  # Print lines.
  for line in lines:
    kind = line.kind
    match = line.match
    text = line.text
    if kind == 'ctx':
      print(text)
    elif kind == 'rem':
      c = C_REM
      if not text: c = C_REM_WS + ERASE_LINE_F
      elif text.isspace(): c = C_REM_WS
      elif line.old_num in old_moved_nums: c = C_REM_MOVED
      print(f'{c}{text}{RST}')
    elif kind == 'add':
      c = C_ADD
      if not text: c = C_ADD_WS + ERASE_LINE_F
      elif text.isspace(): c = C_ADD_WS
      elif line.new_num in new_moved_nums: c = C_ADD_MOVED
      print(f'{c}{text}{RST}')
    elif kind == 'loc':
      new_num = match['new_num']
      hunk_header = match['hunk_header']
      s = f'{RST} {C_HEADER}' if hunk_header else ''
      print(f'{C_LOC}{path}:{new_num}:{s}{hunk_header}{RST}')
    elif kind == 'diff':
      old_path = match['diff_a']
      new_path = match['diff_b']
      msg = new_path if (old_path == new_path) else f'{old_path} -> {new_path}'
      print(f'{C_FILE}{msg}{ERASE_LINE_F}{RST}')
    elif kind == 'meta':
      print(f'{C_MODE}{path}:{RST} {line.rich_text}')
    elif kind in dropped_kinds:
      if interactive: # cannot drop lines, becasue interactive mode slices the diff by line counts.
        print(f'{C_DROPPED}{line.plain_text}{RST}')
    elif kind in pass_kinds:
      print(line.rich_text)
    else:
      raise Exception(f'unhandled kind: {kind}\n{text!r}')


def insert_unique_line(d:Dict[str, Optional[int]], line:str, idx:int) -> None:
  body = line.strip()
  if body in d: d[body] = None
  else: d[body] = idx


dropped_kinds = {
  'idx', 'old', 'new'
}

pass_kinds = {
  'empty', 'other'
}


sgr_pat = re.compile(r'\x1B\[[0-9;]*m')

graph_pat = re.compile(r'(?x) [\ \* \| \\ /]*')

diff_re = r'''(?x)
(?:
  (?P<empty> $)
| (?P<commit>   commit\ [0-9a-z]{40})
| (?P<author>   Author: )
| (?P<date>     Date:   )
| (?P<diff>     diff\ --git\ (a/)?(?P<diff_a>.+)\ (b/)?(?P<diff_b>.+) ) # note that we eat a/ and b/ prefixes, even when using `--no-index`.
| (?P<idx>      index   )
| (?P<old>      ---     )
| (?P<new>      \+\+\+  )
| (?P<loc>      @@\ -(?P<old_num>\d+)(?P<old_len>,\d+)?\ \+(?P<new_num>\d+)(?P<new_len>,\d+)?\ @@\ ?(?P<hunk_header>.*) )
| (?P<ctx>      \  (?P<ctx_text>.*) )
| (?P<rem>      -  (?P<rem_text>.*) )
| (?P<add>      \+ (?P<add_text>.*) )
| (?P<meta>
  ( old\ mode
  | new\ mode
  | deleted\ file\ mode
  | new\ file\ mode
  | copy\ from
  | copy\ to
  | rename\ from
  | rename\ to
  | similarity\ index
  | dissimilarity\ index ) )
| (?P<other> .* )
)
'''

diff_pat = re.compile(diff_re)


# ANSI control sequence indicator.
CSI = '\x1b['

def sgr(*codes:Any) -> str:
  'Select Graphic Rendition control sequence string.'
  code = ';'.join(str(c) for c in codes)
  return f'{CSI}{code}m'

RST = sgr()

RST_BOLD, RST_ULINE, RST_BLINK, RST_INVERT, RST_TXT, RST_BG = (sgr(i) for i in (22, 24, 25, 27, 39, 49))

BOLD, ULINE, BLINK, INVERT = (sgr(i) for i in (1, 4, 5, 7))


# xterm-256 sequence initiators; these should be followed by a single color index.
# both text and background can be specified in a single sgr call.
TXT = '38;5'
BG = '48;5'

# RGB6 color cube: 6x6x6, from black to white.
K = 16  # black.
W = 231 # white.

# Grayscale: the 24 palette values have a suggested 8 bit grayscale range of [8, 238].
middle_gray_indices = range(232, 256)

def gray26(n:int) -> int:
  assert 0 <= n < 26
  if n == 0: return K
  if n == 25: return W
  return W + n

def rgb6(r:int, g:int, b:int) -> int:
  'index RGB triples into the 256-color palette (returns 16 for black, 231 for white).'
  assert 0 <= r < 6
  assert 0 <= g < 6
  assert 0 <= b < 6
  return (((r * 6) + g) * 6) + b + 16


# same-same colors.

C_FILE = sgr(BG, rgb6(1, 0, 1))
C_MODE = sgr(BG, rgb6(0, 3, 4))
C_UNKNOWN = sgr(BG, rgb6(5, 0, 5))
C_LOC = sgr(BG, rgb6(0, 1, 2))

C_REM = sgr(TXT, rgb6(5, 0, 0))
C_ADD = sgr(TXT, rgb6(0, 5, 0))
C_REM_WS = sgr(BG, rgb6(1, 0, 0))
C_ADD_WS = sgr(BG, rgb6(0, 1, 0))
C_REM_MOVED = sgr(TXT, rgb6(2, 0, 0))
C_ADD_MOVED = sgr(TXT, rgb6(0, 2, 0))
C_HEADER = sgr(TXT, gray26(17), BG, gray26(4))
C_DROPPED = sgr(TXT, gray26(10))

ERASE_LINE_F = CSI + 'K'

def errL(*items:Any) -> None: print(*items, sep='', file=stderr)

def errSL(*items:Any) -> None: print(*items, file=stderr)


if __name__ == '__main__': main()
