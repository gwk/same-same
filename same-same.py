#!/usr/bin/env python3

import re
from os import environ
from sys import stderr, stdout
from typing import *
from typing import Match


DiffLine = Tuple[Match, str] # (match, rich_text).


def main() -> None:
  # Git can generate utf8-illegal sequences; ignore them.
  stdin = open(0, errors='replace')

  if 'SAME_SAME_OFF' in environ:
    for line in stdin:
      stdout.write(line)
    exit(0)

  buffer:List[DiffLine] = []

  def flush_buffer() -> None:
    nonlocal buffer
    if buffer:
      handle_file_lines(buffer)
      buffer = []

  try:
    for rich_text in stdin:
      rich_text = rich_text.rstrip('\n')
      text = sgr_pat.sub('', rich_text) # remove colors.
      m = diff_pat.match(text)
      assert m is not None
      kind = get_kind(m)
      if kind == 'diff': flush_buffer()
      buffer.append((m, rich_text))
    flush_buffer()
  except BrokenPipeError:
    stderr.close() # Prevents warning message.


def handle_file_lines(lines:List[DiffLine]) -> None:
  match, rich_text = lines[0]
  kind = get_kind(match)
  text = match.string
  skip = False
  if kind != 'diff': skip = True
  elif graph_pat.match(text).end(): skip = True # type: ignore

  if skip:
    for _, rich_text in lines: print(rich_text)
    return

  old_ctx_nums:Set[int] = set() # Line numbers of context lines.
  new_ctx_nums:Set[int] = set() # ".
  old_texts:Dict[int, str] = {} # Maps of line numbers to text.
  new_texts:Dict[int, str] = {} # ".
  old_uniques:Dict[str, Optional[int]] = {} # Maps unique line bodies to line numbers.
  new_uniques:Dict[str, Optional[int]] = {} # ".
  #moved_pairs = {} # new to old indices.
  old_path = '<OLD_PATH>'
  new_path = '<NEW_PATH>'
  old_num = 0 # 1-indexed.
  new_num = 0 # 1-indexed.
  nums:List[Tuple[Optional[int], Optional[int]]] = [] # ordered (old, new) pairs.

  def append_texts(old_text:Optional[str], new_text:Optional[str]) -> None:
    nonlocal old_num, new_num
    o_i = None
    n_i = None
    assert old_text is not None or new_text is not None
    if old_text is not None:
      assert old_num not in old_texts
      o_i = old_num
      old_num += 1
      old_texts[o_i] = old_text
    if new_text is not None:
      assert new_num not in new_texts
      n_i = new_num
      new_num += 1
      new_texts[n_i] = new_text
    nums.append((o_i, n_i))

  # Accumulate lines into structures.
  for match, rich_text in lines:
    kind = get_kind(match)
    if kind == 'ctx':
      assert old_num not in old_ctx_nums
      old_ctx_nums.add(old_num)
      assert new_num not in new_ctx_nums
      new_ctx_nums.add(new_num)
      t = match['ctx_text']
      append_texts(t, t)
    elif kind == 'rem':
      t = match['rem_text']
      insert_unique_line(old_uniques, t, old_num)
      append_texts(t, None)
    elif kind == 'add':
      t = match['add_text']
      insert_unique_line(new_uniques, t, new_num)
      append_texts(None, t)
    elif kind == 'loc':
      o = int(match['old_num'])
      if o > 0:
        assert o > old_num, (o, old_num, match.string)
        old_num = o
        #old_last = old_num + int(match['old_len']) - 1
      n = int(match['new_num'])
      if n > 0:
        assert n > new_num
        new_num = n
        #new_last = new_num + int(match['new_len']) - 1
      #hunk_header = match['hunk_header']
    elif kind == 'diff':
      old_path = match['diff_a']
      new_path = match['diff_b']
      if old_path != new_path:
        print(f'{C_RENAME}{old_path} -> {new_path}{RST}')
    elif kind == 'meta':
      print(f'{C_MODE}{new_path}:{RST} {rich_text}')
    elif kind in dropped_kinds:
      continue
    elif kind in pass_kinds:
      print(rich_text)
    elif kind == 'unknown':
      print(f'{C_UNKNOWN}{kind.upper()}:{RST} {rich_text!r}')
    else:
      raise Exception(f'unhandled kind: {kind}\n{text!r}')

  # Detect moved lines.

  def diff_lines_match(old_idx, new_idx) -> bool:
    if old_idx in old_ctx_nums or new_idx in new_ctx_nums: return False
    try: return old_texts[old_idx].strip() == new_texts[new_idx].strip()
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
  o_prev = -1
  n_prev = -1
  for o_i, n_i, in nums:
    if o_prev+1 != o_i and n_prev+1 != n_i: # new hunk.
      print(f'{C_LOC}{new_path}:{n_i}:{RST}')
    if o_i: o_prev = o_i
    if n_i: n_prev = n_i
    if n_i is None: # rem line.
      assert o_i is not None
      t = old_texts[o_i]
      c = C_REM_WS if t.isspace() else (C_REM_MOVED if o_i in old_moved_nums else C_REM)
      print(f'{c}{t}{RST}')
    elif o_i is None: # add line.
      t = new_texts[n_i]
      c = C_ADD_WS if t.isspace() else (C_ADD_MOVED if n_i in new_moved_nums else C_ADD)
      print(f'{c}{t}{RST}')
    else: # ctx line.
      t = new_texts[n_i]
      print(f'{t}')


def insert_unique_line(d:Dict[str, Optional[int]], line:str, idx:int) -> None:
  body = line.strip()
  if body in d: d[body] = None
  else: d[body] = idx


def get_kind(match:Match) -> str:
  kind:Optional[str] = match.lastgroup
  assert kind is not None, match
  return kind


dropped_kinds = {
  'idx', 'old', 'new'
}

pass_kinds = {
  'empty',
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
| (?P<loc>      @@\ -(?P<old_num>\d+),(?P<old_len>\d+)\ \+(?P<new_num>\d+),(?P<new_len>\d+)\ @@\ ?(?P<hunk_header>.*) )
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
| (?P<unknown> .* )
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


# xterm-256 sequence initiators; these should be followed by a single color index.
# both text and background can be specified in a single sgr call.
TXT = '38;5'
BG = '48;5'

# RGB6 color cube: 6x6x6, from black to white.
K = 16  # black.
W = 231 # white.

# Grayscale: the 24 palette values have a suggested 8 bit grayscale range of [8, 238].
middle_gray_indices = range(232, 256)
K1, K2, K3, K4, K5, K6, K7, K8, K9, KA, KB, KC, \
WC, WB, WA, W9, W8, W7, W6, W5, W4, W3, W2, W1, \
= middle_gray_indices

gray_indices = (K, *middle_gray_indices, W) # Include black and white.


def rgb6(r:int, g:int, b:int) -> int:
  'index RGB triples into the 256-color palette (returns 16 for black, 231 for white).'
  assert 0 <= r < 6
  assert 0 <= g < 6
  assert 0 <= b < 6
  return (((r * 6) + g) * 6) + b + 16


# same-same colors.

C_RENAME = sgr(BG, rgb6(0, 2, 1))
C_MODE = sgr(BG, rgb6(0, 3, 4))
C_UNKNOWN = sgr(BG, rgb6(5, 0, 5))
C_LOC = sgr(BG, rgb6(0, 1, 2))

C_REM = sgr(TXT, rgb6(5, 0, 0))
C_ADD = sgr(TXT, rgb6(0, 5, 0))
C_REM_WS = sgr(BG, rgb6(1, 0, 0))
C_ADD_WS = sgr(BG, rgb6(0, 1, 0))
C_REM_MOVED = sgr(TXT, rgb6(2, 0, 0))
C_ADD_MOVED = sgr(TXT, rgb6(0, 2, 0))


if __name__ == '__main__': main()
