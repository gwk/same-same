#!/usr/bin/env python3
# Dedicated to the public domain under CC0: https://creativecommons.org/publicdomain/zero/1.0/.

import re
from argparse import ArgumentParser
from difflib import SequenceMatcher
from itertools import chain, groupby
from os import environ
from sys import stderr, stdout
from typing import *
from typing import Match


class DiffLine:
  def __init__(self, kind:str, match:Match, rich_text:str) -> None:
    self.kind = kind # The name from `diff_pat` named capture groups.
    self.match = match
    self.rich_text = rich_text # Original colorized text from git.
    self.old_num = 0 # 1-indexed.
    self.new_num = 0 # ".
    self.chunk_idx = 0 # Positive for rem/add.
    self.is_src = False # True for ctx/rem/add.
    self.text = '' # Final text for ctx/rem/add.

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

  def flush_buffer() -> None:
    nonlocal buffer
    if buffer:
      handle_file_lines(buffer, interactive=args.interactive)
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
      buffer.append(DiffLine(kind, match, rich_text))
    flush_buffer()
  except BrokenPipeError:
    stderr.close() # Prevents warning message.


def handle_file_lines(lines:List[DiffLine], interactive:bool) -> None:
  first = lines[0]
  kind = first.kind
  skip = False

  # Detect if we should skip these lines.
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
  old_num = 0 # 1-indexed source line number.
  new_num = 0 # ".
  chunk_idx = 0 # Counter to differentiate chunks; becomes part of the groupby key.

  # Accumulate source lines into structures.
  old_path = '<OLD_PATH>'
  new_path = '<NEW_PATH>'
  is_prev_add_rem = False
  for line in lines:
    match = line.match
    kind = line.kind
    is_add_rem = (kind in ('rem', 'add'))
    if not is_prev_add_rem and is_add_rem: chunk_idx += 1
    is_prev_add_rem = is_add_rem
    if kind in ('ctx', 'rem', 'add'):
      line.is_src = True
      if kind == 'ctx':
        line.text = match['ctx_text']
      elif kind == 'rem':
        line.text = match['rem_text']
        line.chunk_idx = chunk_idx
        insert_unique_line(old_uniques, line.text, old_num)
      elif kind == 'add':
        line.text = match['add_text']
        line.chunk_idx = chunk_idx
        insert_unique_line(new_uniques, line.text, new_num)
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
    elif kind == 'loc':
      o = int(match['old_num'])
      if o > 0:
        assert o > old_num, (o, old_num, match.string)
        old_num = o
      n = int(match['new_num'])
      if n > 0:
        assert n > new_num
        new_num = n
    elif kind == 'old': old_path = vscode_path(match['old_path'].rstrip('\t'))
    elif kind == 'new': new_path = vscode_path(match['new_path'].rstrip('\t')) # Not sure why this trailing tab appears.

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

  # Break lines into rem/add chunks.
  # While a "hunk" is a series of (possibly many) ctx/rem/add lines provided by git diff,
  # a "chunk" is either a contiguous block of rem/add lines, or else any other single line.
  # This approach simplifies the token diffing process so that it is a reasonably
  # straightforward comparison of a rem block to an add block.

  def chunk_key(line:DiffLine) -> Tuple[int, bool]:
    return (line.is_src, line.chunk_idx, (line.old_num in old_moved_nums or line.new_num in new_moved_nums))

  for ((is_src, chunk_idx, is_moved), _chunk) in groupby(lines, key=chunk_key):
    chunk = list(_chunk) # We iterate over the sequence several times.
    if chunk_idx and not is_moved: # Chunk should be diffed by tokens.
      # We must ensure that the same number of lines is output, at least for `-interactive` mode.
      # Currently, we do not reorder lines at all, but that is an option for the future.
      rem_lines = [l for l in chunk if l.old_num]
      add_lines = [l for l in chunk if l.new_num]
      add_token_diffs(rem_lines, add_lines)
    elif is_src: # ctx or moved.
      for l in chunk:
        l.text = highlight_strange_chars(l.text)

    # Print lines.
    for line in chunk:
      kind = line.kind
      match = line.match
      text = line.text
      if kind == 'ctx':
        print(text)
      elif kind == 'rem':
        m = C_REM_MOVED if line.old_num in old_moved_nums else ''
        print(C_REM_LINE, m, text, C_END, sep='')
      elif kind == 'add':
        m = C_ADD_MOVED if line.new_num in new_moved_nums else ''
        print(C_ADD_LINE, m, text, C_END, sep='')
      elif kind == 'loc':
        new_num = match['new_num']
        snippet = match['parent_snippet']
        s = ' ' + C_SNIPPET if snippet else ''
        print(C_LOC, new_path, ':', new_num, ':', s, snippet, C_END, sep='')
      elif kind == 'diff':
        msg = new_path if (old_path == new_path) else '{} -> {}'.format(old_path, new_path)
        print(C_FILE, msg, ':', C_END, sep='')
      elif kind == 'meta':
        print(C_MODE, new_path, ':', RST, ' ', line.rich_text, sep='')
      elif kind in dropped_kinds:
        if interactive: # cannot drop lines, becasue interactive mode slices the diff by line counts.
          print(C_DROPPED, line.plain_text, RST, sep='')
      elif kind in pass_kinds:
        print(line.rich_text)
      else:
        raise Exception('unhandled kind: {}\n{!r}'.format(kind, text))


def insert_unique_line(d:Dict[str, Optional[int]], line:str, idx:int) -> None:
  'For the purpose of movement detection, lines are tested for uniqueness after stripping leading and trailing whitespace.'
  body = line.strip()
  if body in d: d[body] = None
  else: d[body] = idx


def add_token_diffs(rem_lines:List[DiffLine], add_lines:List[DiffLine]) -> None:
  'Rewrite DiffLine.text values to include per-token diff highlighting.'
  # Get lists of tokens for the entire chunk.
  r_tokens = tokenize_difflines(rem_lines)
  a_tokens = tokenize_difflines(add_lines)
  m = SequenceMatcher(isjunk=is_token_junk, a=r_tokens, b=a_tokens, autojunk=True)
  r_frags:List[List[str]] = [[] for _ in rem_lines] # Accumulate highlighted tokens.
  a_frags:List[List[str]] = [[] for _ in add_lines]
  r_line_idx = 0 # Step through the accumulators.
  a_line_idx = 0
  r_d = 0 # Token index of previous/next diff.
  a_d = 0
  # TODO: r_lit, a_lit flags could slightly reduce emission of color sequences.
  blocks = m.get_matching_blocks() # last block is the sentinel: (len(a), len(b), 0).
  for r_p, a_p, l in m.get_matching_blocks():
    # Highlight the differing tokens.
    r_line_idx = append_frags(r_frags, r_tokens, r_line_idx, r_d, r_p, C_REM_TOKEN)
    a_line_idx = append_frags(a_frags, a_tokens, a_line_idx, a_d, a_p, C_ADD_TOKEN)
    r_d = r_p+l # update to end of match / beginning of next diff.
    a_d = a_p+l
    # Do not highlight the matching tokens.
    r_line_idx = append_frags(r_frags, r_tokens, r_line_idx, r_p, r_d, C_RST_TOKEN)
    a_line_idx = append_frags(a_frags, a_tokens, a_line_idx, a_p, a_d, C_RST_TOKEN)
  for rem_line, frags in zip(rem_lines, r_frags):
    rem_line.text = ''.join(frags)
  for add_line, frags in zip(add_lines, a_frags):
    add_line.text = ''.join(frags)


def tokenize_difflines(lines:List[DiffLine]) -> List[str]:
  'Convert the list of line texts into a single list of tokens, including newline tokens.'
  tokens:List[str] = []
  for i, line in enumerate(lines):
    if i: tokens.append('\n')
    tokens.extend(m[0] for m in token_pat.finditer(line.text))
  return tokens


def is_token_junk(token:str) -> bool:
  '''
  Treate newlines as tokens, but all other whitespace as junk.
  This forces the diff algorithm to respect line breaks but not get distracted aligning to whitespace.
  '''
  return token.isspace() and token != '\n'


def append_frags(frags:List[List[str]], tokens:List[str], line_idx:int, pos:int, end:int, highlight:str) -> int:
  for frag in tokens[pos:end]:
    if frag == '\n':
      line_idx += 1
    else:
      line_frags = frags[line_idx]
      line_frags.append(highlight)
      line_frags.append(highlight_strange_chars(frag))
  return line_idx


def highlight_strange_chars(string:str) -> str:
  return strange_char_pat.sub(
    lambda m: '{}{}{}'.format(C_STRANGE, m[0].translate(strange_char_trans_table), C_RST_STRANGE),
    string)


dropped_kinds = {
  'idx', 'old', 'new'
}

pass_kinds = {
  'empty', 'other'
}


sgr_pat = re.compile(r'\x1B\[[0-9;]*m')

graph_pat = re.compile(r'(?x) [ /\*\|\\]*') # space is treated as literal inside of brackets, even in extended mode.

diff_pat = re.compile(r'''(?x)
(?:
  (?P<empty> $)
| (?P<commit>   commit\ [0-9a-z]{40} )
| (?P<author>   Author: )
| (?P<date>     Date:   )
| (?P<diff>     diff\ --git )
| (?P<idx>      index   )
| (?P<old>      ---     \ (?P<old_path>.+) )
| (?P<new>      \+\+\+  \ (?P<new_path>.+) )
| (?P<loc>      @@\ -(?P<old_num>\d+)(?P<old_len>,\d+)?\ \+(?P<new_num>\d+)(?P<new_len>,\d+)?\ @@\ ?(?P<parent_snippet>.*) )
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
''')


token_pat = re.compile(r'''(?x)
  \w[\w\d]* # Symbol token.
| \d+ # Number token.
| \ + # Spaces; distinct from other whitespace.
| \t+ # Tabs; distinct from other whitespace.
| \s+ # Other whitespace.
| . # Any other single character; newlines are never present so DOTALL is irrelevant.
''')


# Unicode ranges for strange characters:
# C0:   \x00 - \x1F
# \n:   \x0A
# C0 !\n: [ \x00-\x09 \x0B-\x1F ]
# SP:   \x20
# DEL:  \x7F
# C1:   \x80 - \x9F
# NBSP: \xA0 (nonbreaking space)
# SHY:  \xAD (soft hyphen)
strange_char_re = r'(?x) [\x00-\x09\x0B-\x1F\x7F\x80-\x9F\xA0\xAD]+'
strange_char_pat = re.compile(strange_char_re)
assert not strange_char_pat.match(' ')

strange_char_ords = chain(range(0, 0x09+1), range(0x0B, 0x1F+1), range(0x7F, 0x7F+1),
  range(0x80, 0x9F+1), range(0xA0, 0xA0+1), range(0xAD, 0xAD+1))
assert ord(' ') not in strange_char_ords
strange_char_names = { i : '\\x{:02x}'.format(i) for i in strange_char_ords }
strange_char_names.update({
  '\0' : '\\0',
  '\a' : '\\a',
  '\b' : '\\b',
  '\f' : '\\f',
  '\r' : '\\r',
  '\t' : '\\t',
  '\v' : '\\v',
})

strange_char_trans_table = str.maketrans(strange_char_names)


# ANSI control sequence indicator.
CSI = '\x1b['

ERASE_LINE_F = CSI + 'K' # Sending erase line forward while background color is set colors to end of line.

def sgr(*codes:Any) -> str:
  'Select Graphic Rendition control sequence string.'
  code = ';'.join(str(c) for c in codes)
  return '\x1b[{}m'.format(code)

RST = sgr()

RST_BOLD, RST_ULINE, RST_BLINK, RST_INVERT, RST_TXT, RST_BG = (22, 24, 25, 27, 39, 49)

BOLD, ULINE, BLINK, INVERT = (1, 4, 5, 7)


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
C_MODE = sgr(BG, rgb6(1, 0, 1))
C_LOC = sgr(BG, rgb6(0, 1, 2))
C_UNKNOWN = sgr(BG, rgb6(5, 0, 5))
C_SNIPPET = sgr(TXT, gray26(22))
C_DROPPED = sgr(TXT, gray26(10))

C_REM_LINE = sgr(BG, rgb6(1, 0, 0))
C_ADD_LINE = sgr(BG, rgb6(0, 1, 0))
C_REM_MOVED = sgr(TXT, rgb6(4, 2, 0))
C_ADD_MOVED = sgr(TXT, rgb6(2, 4, 0))
C_REM_TOKEN = sgr(TXT, rgb6(5, 2, 3), BOLD)
C_ADD_TOKEN = sgr(TXT, rgb6(2, 5, 3), BOLD)

C_RST_TOKEN = sgr(RST_TXT, RST_BOLD)

C_STRANGE = sgr(INVERT)
C_RST_STRANGE = sgr(RST_INVERT)

C_END = ERASE_LINE_F + RST


def vscode_path(path:str) -> str:
  'VSCode will only recognize source locations if the path contains a slash; add "./" to plain file names.'
  if '/' in path or '<' in path or '>' in path: return path # Do not alter pseudo-names like <stdin>.
  return './' + path

def errL(*items:Any) -> None: print(*items, sep='', file=stderr)

def errSL(*items:Any) -> None: print(*items, file=stderr)


if __name__ == '__main__': main()
