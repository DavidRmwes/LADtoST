#!/usr/bin/env python3
"""
l5x_lad2st.py — Allen-Bradley L5X/L5K Ladder Logic (RLL) → Structured Text Converter

Converts RLL (Relay Ladder Logic) routines exported from Studio 5000 Logix Designer
in L5X (XML) or L5K (text) format into equivalent IEC 61131-3 Structured Text.

Usage:
    python l5x_lad2st.py input.L5X                         # Combined .st output
    python l5x_lad2st.py input.L5K                         # Also works with L5K
    python l5x_lad2st.py input.L5X -o output.st            # Custom output path
    python l5x_lad2st.py input.L5X --split                 # One .st file per routine
    python l5x_lad2st.py input.L5X --routines Auto_Sequence MainRoutine
    python l5x_lad2st.py input.L5X --list                  # List routines only
    python l5x_lad2st.py input.L5X --verbose               # Detailed conversion log
    python l5x_lad2st.py input.L5X --strip-nop             # Omit NOP-only rungs
    python l5x_lad2st.py input.L5X --simplify              # Optimize EQU(X,X) etc.

Author:  Auto-generated conversion engine
License: MIT
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import textwrap
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

__version__ = "2.0.0"

log = logging.getLogger("lad2st")


# ═══════════════════════════════════════════════════════════════════════════════
#  SECTION 1 — INSTRUCTION TABLES
# ═══════════════════════════════════════════════════════════════════════════════

CONDITION_INSTRUCTIONS = {
    "XIC", "XIO",
    "EQU", "NEQ", "GEQ", "LEQ", "GRT", "LES", "LIM",
}

DUAL_INSTRUCTIONS = {"ONS"}

KNOWN_OUTPUT_INSTRUCTIONS = {
    "OTE", "OTL", "OTU",
    "MOV", "CLR", "ADD", "SUB", "MUL", "DIV", "MOD", "CPT", "SQR", "ABS", "NEG",
    "NOT", "AND", "OR", "XOR", "BTD", "BTDT", "MVMT",
    "COP", "FLL",
    "TON", "TOF", "RTO", "CTU", "CTD", "RES",
    "JSR", "RET", "JMP", "LBL", "NOP", "AFI",
    "MSG",
    "GSV", "SSV",
    "CONCAT", "INSERT", "DELETE", "MID", "FIND", "DTOS", "STOD", "UPPER", "LOWER",
    "FSC", "FAL", "CMP",
    "AVE", "SRT", "SIZE",
    "SWPB", "SETDTO", "EVENT", "EOT",
    "ABL", "AHL", "ACB", "ACL", "ARD", "ARL", "AWA", "AWT",
}


def is_condition(name: str) -> bool:
    return name in CONDITION_INSTRUCTIONS or name in DUAL_INSTRUCTIONS


# ═══════════════════════════════════════════════════════════════════════════════
#  SECTION 2 — TOKENIZER
# ═══════════════════════════════════════════════════════════════════════════════

def tokenize_rung(text: str) -> list[tuple]:
    tokens = []
    i = 0
    text = text.strip().rstrip(";")
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "[":
            tokens.append(("[", None)); i += 1
        elif c == "]":
            tokens.append(("]", None)); i += 1
        elif c == ",":
            tokens.append((",", None)); i += 1
        else:
            m = re.match(r"([A-Za-z_]\w*)\(", text[i:])
            if m:
                name = m.group(1)
                start = i + len(name) + 1
                depth, j = 1, start
                while j < n and depth > 0:
                    if text[j] == "(":   depth += 1
                    elif text[j] == ")": depth -= 1
                    j += 1
                args = _split_args(text[start : j - 1])
                tokens.append(("INSTR", (name.upper(), args)))
                i = j
            else:
                i += 1
    return tokens


def _split_args(s: str) -> list[str]:
    args, depth, cur = [], 0, ""
    for c in s:
        if c == "(":   depth += 1; cur += c
        elif c == ")": depth -= 1; cur += c
        elif c == "," and depth == 0:
            args.append(cur.strip()); cur = ""
        else:
            cur += c
    if cur.strip():
        args.append(cur.strip())
    return args


# ═══════════════════════════════════════════════════════════════════════════════
#  SECTION 3 — AST
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class InstrNode:
    name: str
    args: list[str]
    def __repr__(self): return f"{self.name}({', '.join(self.args)})"

@dataclass
class SeriesNode:
    children: list = field(default_factory=list)

@dataclass
class BranchNode:
    branches: list = field(default_factory=list)


class Parser:
    def __init__(self, tokens: list[tuple]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Optional[tuple]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def consume(self, expect=None) -> tuple:
        tok = self.tokens[self.pos]
        if expect and tok[0] != expect:
            raise ValueError(f"Expected {expect!r}, got {tok!r} at pos {self.pos}")
        self.pos += 1
        return tok

    def parse(self) -> SeriesNode:
        series = SeriesNode()
        while self.pos < len(self.tokens):
            tok = self.peek()
            if tok is None or tok[0] in ("]", ","):
                break
            if tok[0] == "[":
                series.children.append(self._parse_branch())
            elif tok[0] == "INSTR":
                self.consume()
                series.children.append(InstrNode(tok[1][0], tok[1][1]))
            else:
                self.pos += 1
        return series

    def _parse_branch(self) -> BranchNode:
        self.consume("[")
        branch = BranchNode()
        branch.branches.append(self.parse())
        while self.pos < len(self.tokens) and self.peek() and self.peek()[0] == ",":
            self.consume(",")
            branch.branches.append(self.parse())
        if self.pos < len(self.tokens) and self.peek() and self.peek()[0] == "]":
            self.consume("]")
        return branch


# ═══════════════════════════════════════════════════════════════════════════════
#  SECTION 4 — CONDITION RENDERING
# ═══════════════════════════════════════════════════════════════════════════════

def _cond(node: InstrNode, simplify: bool = False) -> str:
    n, a = node.name, node.args
    if n == "XIC": return a[0]
    if n == "XIO": return f"NOT {a[0]}"
    if n == "EQU":
        if simplify and a[0] == a[1]:
            return "TRUE"
        return f"({a[0]} = {a[1]})"
    if n == "NEQ": return f"({a[0]} <> {a[1]})"
    if n == "GEQ": return f"({a[0]} >= {a[1]})"
    if n == "LEQ": return f"({a[0]} <= {a[1]})"
    if n == "GRT": return f"({a[0]} > {a[1]})"
    if n == "LES": return f"({a[0]} < {a[1]})"
    if n == "LIM": return f"({a[0]} <= {a[1]} AND {a[1]} <= {a[2]})"
    if n == "ONS": return a[0]
    return f"{n}({', '.join(a)})"


# ═══════════════════════════════════════════════════════════════════════════════
#  SECTION 5 — ACTION RENDERING
# ═══════════════════════════════════════════════════════════════════════════════

def _act(node: InstrNode, ind: str = "    ") -> str:
    n, a = node.name, node.args
    j = ", ".join(a)

    if n == "NOP": return f"{ind}; (* NOP *)"
    if n == "AFI": return f"{ind}; (* AFI — always false *)"
    if n == "OTE": return f"{ind}{a[0]} := 1;"
    if n == "OTL": return f"{ind}{a[0]} := 1; (* Latch *)"
    if n == "OTU": return f"{ind}{a[0]} := 0; (* Unlatch *)"

    if n == "MOV": return f"{ind}{a[1]} := {a[0]};"
    if n == "CLR": return f"{ind}{a[0]} := 0;"
    if n == "ADD": return f"{ind}{a[2]} := {a[0]} + {a[1]};"
    if n == "SUB": return f"{ind}{a[2]} := {a[0]} - {a[1]};"
    if n == "MUL": return f"{ind}{a[2]} := {a[0]} * {a[1]};"
    if n == "DIV": return f"{ind}{a[2]} := {a[0]} / {a[1]};"
    if n == "MOD": return f"{ind}{a[2]} := {a[0]} MOD {a[1]};"
    if n == "CPT": return f"{ind}{a[0]} := {a[1]}; (* Compute *)"
    if n == "SQR": return f"{ind}{a[1]} := SQRT({a[0]});"
    if n == "ABS": return f"{ind}{a[1]} := ABS({a[0]});"
    if n == "NEG": return f"{ind}{a[1]} := -{a[0]};"

    if n == "NOT": return f"{ind}{a[1]} := NOT {a[0]};"
    if n == "AND": return f"{ind}{a[2]} := {a[0]} AND {a[1]};"
    if n == "OR":  return f"{ind}{a[2]} := {a[0]} OR {a[1]};"
    if n == "XOR": return f"{ind}{a[2]} := {a[0]} XOR {a[1]};"
    if n == "BTD": return f"{ind}BTD({j});"

    if n == "COP": return f"{ind}COP({j});"
    if n == "FLL": return f"{ind}FLL({j});"

    if n in ("TON", "TOF", "RTO"): return f"{ind}{n}({j});"
    if n in ("CTU", "CTD"):        return f"{ind}{n}({j});"
    if n == "RES":                  return f"{ind}{a[0]}.RES; (* Reset *)"

    if n == "JSR": return f"{ind}JSR({j});"
    if n == "RET": return f"{ind}RETURN;"
    if n == "JMP": return f"{ind}(* JMP({a[0]}) — REVIEW: restructure as IF/ELSIF or CASE *)"
    if n == "LBL": return f"{ind}(* LBL({a[0]}) — REVIEW: restructure as IF/ELSIF or CASE *)"

    if n == "MSG": return f"{ind}MSG({j});"
    if n == "GSV": return f"{ind}GSV({j});"
    if n == "SSV": return f"{ind}SSV({j});"

    if n in ("CONCAT", "INSERT", "DELETE", "MID", "FIND",
             "DTOS", "STOD", "UPPER", "LOWER"):
        return f"{ind}{n}({j});"

    if n == "FSC":
        return f"{ind}FSC({j}); (* REVIEW: consider FOR loop equivalent *)"
    if n == "FAL":
        return f"{ind}FAL({j}); (* REVIEW: consider FOR loop equivalent *)"

    if n == "SIZE": return f"{ind}SIZE({j});"
    if n == "SRT":  return f"{ind}SRT({j});"
    if n == "AVE":  return f"{ind}AVE({j});"

    if n == "SWPB":   return f"{ind}SWPB({j});"
    if n == "SETDTO": return f"{ind}SETDTO({j});"
    if n == "EVENT":  return f"{ind}EVENT({j});"
    if n == "EOT":    return f"{ind}EOT();"

    if n == "ONS":
        return f"{ind}(* ONS({a[0]}) — one-shot; implement with R_TRIG *)"

    return f"{ind}{n}({j});"


# ═══════════════════════════════════════════════════════════════════════════════
#  SECTION 6 — AST ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def _extract(node, simplify: bool = False):
    if isinstance(node, InstrNode):
        if is_condition(node.name):
            return (_cond(node, simplify), [])
        return (None, [node])

    if isinstance(node, SeriesNode):
        conds, acts = [], []
        for child in node.children:
            c, a = _extract(child, simplify)
            if c:
                conds.append(c)
            acts.extend(a)
        cs = " AND ".join(conds) if conds else None
        return (cs, acts)

    if isinstance(node, BranchNode):
        bconds, bacts = [], []
        all_cond_only = True
        for br in node.branches:
            c, a = _extract(br, simplify)
            bconds.append(c)
            bacts.append(a)
            if a:
                all_cond_only = False
        if all_cond_only:
            valid = [c for c in bconds if c]
            if not valid:
                return (None, [])
            expr = valid[0] if len(valid) == 1 else "(" + " OR ".join(valid) + ")"
            return (expr, [])
        return (None, [("BRANCH", node.branches, bconds, bacts)])

    return (None, [])


# ═══════════════════════════════════════════════════════════════════════════════
#  SECTION 7 — RUNG → ST
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ConversionStats:
    routines: int = 0
    rungs_total: int = 0
    rungs_converted: int = 0
    rungs_nop: int = 0
    parse_errors: int = 0
    conversion_errors: int = 0
    review_items: list = field(default_factory=list)

    def summary_text(self) -> str:
        lines = [
            f"Routines:           {self.routines}",
            f"Rungs total:        {self.rungs_total}",
            f"  Converted:        {self.rungs_converted}",
            f"  NOP/empty:        {self.rungs_nop}",
            f"  Parse errors:     {self.parse_errors}",
            f"  Conversion errors:{self.conversion_errors}",
        ]
        if self.review_items:
            lines.append(f"Review items:       {len(self.review_items)}")
            for item in self.review_items:
                lines.append(f"  • {item}")
        return "\n".join(lines)


def _simplify_condition(cond_str: str) -> str:
    parts = [p.strip() for p in cond_str.split(" AND ")]
    filtered = [p for p in parts if p != "TRUE"]
    if not filtered:
        return "TRUE"
    return " AND ".join(filtered)


def convert_rung(
    text: str,
    rung_num: str,
    comment: Optional[str],
    stats: ConversionStats,
    routine_name: str,
    *,
    strip_nop: bool = False,
    simplify: bool = False,
) -> Optional[str]:
    lines: list[str] = []

    if comment:
        for cline in comment.strip().splitlines():
            lines.append(f"(* {cline.rstrip()} *)")

    clean = text.strip().rstrip(";").strip()
    if clean in ("NOP()", "", "AFI()"):
        stats.rungs_nop += 1
        if strip_nop:
            return None
        tag = "NOP" if "NOP" in clean else ("AFI" if "AFI" in clean else "empty")
        lines.append(f"(* Rung {rung_num}: {tag} *)")
        return "\n".join(lines)

    try:
        tokens = tokenize_rung(text)
        ast = Parser(tokens).parse()
    except Exception as exc:
        stats.parse_errors += 1
        lines.append(f"(* Rung {rung_num}: PARSE ERROR — {exc} *)")
        lines.append(f"(* Original: {text.strip()} *)")
        return "\n".join(lines)

    try:
        st = _ast_to_st(ast, rung_num, stats, routine_name, simplify=simplify)
        stats.rungs_converted += 1
        lines.append(st)
    except Exception as exc:
        stats.conversion_errors += 1
        lines.append(f"(* Rung {rung_num}: CONVERSION ERROR — {exc} *)")
        lines.append(f"(* Original: {text.strip()} *)")

    return "\n".join(lines)


REVIEW_INSTRUCTIONS = {
    "ONS": "needs R_TRIG / rising-edge implementation",
    "JMP": "restructure to IF/ELSIF or CASE",
    "LBL": "restructure to IF/ELSIF or CASE",
    "FSC": "replace with FOR loop",
    "FAL": "replace with FOR loop",
}


def _scan_for_review_items(node, stats: ConversionStats, routine: str, rung: str):
    if isinstance(node, InstrNode):
        if node.name in REVIEW_INSTRUCTIONS:
            detail = REVIEW_INSTRUCTIONS[node.name]
            tag_info = f"({node.args[0]})" if node.args else ""
            item = f"{routine} Rung {rung}: {node.name}{tag_info} — {detail}"
            if item not in stats.review_items:
                stats.review_items.append(item)
    elif isinstance(node, (SeriesNode, BranchNode)):
        children = node.children if isinstance(node, SeriesNode) else node.branches
        for child in children:
            _scan_for_review_items(child, stats, routine, rung)


def _ast_to_st(
    ast: SeriesNode,
    rung_num: str,
    stats: ConversionStats,
    routine_name: str,
    *,
    simplify: bool = False,
) -> str:
    lines: list[str] = []

    conditions, actions = [], []
    for child in ast.children:
        if isinstance(child, InstrNode) and is_condition(child.name):
            conditions.append(_cond(child, simplify))
        elif isinstance(child, InstrNode):
            actions.append(child)
        else:
            c, a = _extract(child, simplify)
            if c:
                conditions.append(c)
            actions.extend(a)

    cond_str = " AND ".join(conditions) if conditions else None
    if cond_str and simplify:
        cond_str = _simplify_condition(cond_str)
        if cond_str == "TRUE":
            cond_str = None

    has_ote = any(isinstance(a, InstrNode) and a.name == "OTE" for a in actions)

    _scan_for_review_items(ast, stats, routine_name, rung_num)

    if not actions:
        if cond_str:
            lines.append(f"(* Rung {rung_num}: condition only — {cond_str} *)")
        else:
            lines.append(f"(* Rung {rung_num}: empty *)")
    elif cond_str:
        lines.append(f"IF {cond_str} THEN")
        for action in actions:
            if isinstance(action, InstrNode):
                lines.append(_act(action))
            elif isinstance(action, tuple) and action[0] == "BRANCH":
                lines.extend(_render_branch_actions(action, "    "))
        if has_ote:
            tags = [a.args[0] for a in actions if isinstance(a, InstrNode) and a.name == "OTE"]
            lines.append("ELSE")
            for t in tags:
                lines.append(f"    {t} := 0;")
        lines.append("END_IF;")
    else:
        for action in actions:
            if isinstance(action, InstrNode):
                lines.append(_act(action, ""))
            elif isinstance(action, tuple) and action[0] == "BRANCH":
                lines.extend(_render_branch_actions(action, ""))

    return "\n".join(lines)


def _render_branch_actions(branch_tuple, ind: str) -> list[str]:
    lines = []
    _, _branches, bconds, bacts = branch_tuple
    for cond, acts in zip(bconds, bacts):
        effective_cond = cond if (cond and cond != "TRUE") else None
        for act in acts:
            if isinstance(act, InstrNode):
                if effective_cond:
                    lines += [f"{ind}IF {effective_cond} THEN", _act(act, ind + "    "), f"{ind}END_IF;"]
                else:
                    lines.append(_act(act, ind))
            elif isinstance(act, tuple) and act[0] == "BRANCH":
                sub = _render_branch_actions(act, ind + "    ")
                if effective_cond:
                    lines += [f"{ind}IF {effective_cond} THEN"] + sub + [f"{ind}END_IF;"]
                else:
                    lines.extend(sub)
    return lines


# ═══════════════════════════════════════════════════════════════════════════════
#  SECTION 8 — L5X PARSER
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Rung:
    number: str
    rtype: str
    comment: Optional[str]
    text: str

@dataclass
class Routine:
    name: str
    program: str
    rtype: str
    rungs: list[Rung]


def parse_l5x(filepath: str) -> list[Routine]:
    tree = ET.parse(filepath)
    root = tree.getroot()
    routines: list[Routine] = []

    for program in root.iter("Program"):
        prog_name = program.get("Name", "Unknown")
        for relem in program.findall(".//Routine"):
            rname = relem.get("Name", "Unknown")
            rtype = relem.get("Type", "Unknown")

            rungs = []
            rll = relem.find("RLLContent")
            if rll is not None:
                for re_ in rll.findall("Rung"):
                    ce = re_.find("Comment")
                    te = re_.find("Text")
                    rungs.append(Rung(
                        number=re_.get("Number", "?"),
                        rtype=re_.get("Type", "N"),
                        comment=ce.text.strip() if ce is not None and ce.text else None,
                        text=te.text.strip() if te is not None and te.text else "",
                    ))
            routines.append(Routine(rname, prog_name, rtype, rungs))

    return routines


# ═══════════════════════════════════════════════════════════════════════════════
#  SECTION 8b — L5K PARSER
# ═══════════════════════════════════════════════════════════════════════════════

def _l5k_unescape_comment(raw: str) -> str:
    s = raw
    s = s.replace("$N", "\n")
    s = s.replace("$Q", '"')
    s = s.replace("$'", "'")
    s = s.replace("$$", "$")
    s = s.replace("$R", "\r")
    s = s.replace("$T", "\t")
    lines = s.split("\n")
    cleaned = []
    for line in lines:
        if line.strip() == "" and cleaned and cleaned[-1].strip() == "":
            continue
        cleaned.append(line)
    while cleaned and cleaned[-1].strip() == "":
        cleaned.pop()
    return "\n".join(cleaned)


def parse_l5k(filepath: str) -> list[Routine]:
    with open(filepath, "r", encoding="utf-8-sig") as f:
        raw_lines = f.readlines()

    lines = [ln.rstrip() for ln in raw_lines]

    routines: list[Routine] = []
    current_program = "Unknown"
    current_routine_name = None
    current_routine_type = None
    current_rungs: list[Rung] = []
    pending_comment: Optional[str] = None
    rung_counter = 0

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        m = re.match(r"PROGRAM\s+(\S+)", stripped)
        if m:
            current_program = m.group(1)
            i += 1
            continue

        if stripped.startswith("END_PROGRAM"):
            current_program = "Unknown"
            i += 1
            continue

        m_st = re.match(r"ST_ROUTINE\s+(\S+)", stripped)
        m_rll = re.match(r"ROUTINE\s+(\S+)", stripped)
        if m_st:
            current_routine_name = m_st.group(1)
            current_routine_type = "ST"
            current_rungs = []
            rung_counter = 0
            pending_comment = None
            i += 1
            continue
        elif m_rll:
            current_routine_name = m_rll.group(1)
            current_routine_type = "RLL"
            current_rungs = []
            rung_counter = 0
            pending_comment = None
            i += 1
            continue

        if stripped.startswith("END_ROUTINE"):
            if current_routine_name and current_routine_type:
                routines.append(Routine(
                    name=current_routine_name,
                    program=current_program,
                    rtype=current_routine_type,
                    rungs=current_rungs,
                ))
            current_routine_name = None
            current_routine_type = None
            pending_comment = None
            i += 1
            continue

        if current_routine_name and current_routine_type == "RLL":
            m_rc = re.match(r'RC:\s*"(.*)', stripped)
            if m_rc:
                comment_text, i = _collect_l5k_string(lines, i, m_rc.group(1))
                pending_comment = _l5k_unescape_comment(comment_text)
                continue

            m_n = re.match(r"([A-Z]):\s*(.*)", stripped)
            if m_n:
                rung_type_char = m_n.group(1)
                rung_text = m_n.group(2).rstrip(";").strip()
                current_rungs.append(Rung(
                    number=str(rung_counter),
                    rtype=rung_type_char,
                    comment=pending_comment,
                    text=rung_text,
                ))
                rung_counter += 1
                pending_comment = None
                i += 1
                continue

        i += 1

    return routines


def _collect_l5k_string(lines: list[str], start_idx: int, first_content: str) -> tuple[str, int]:
    parts = []
    text = first_content
    if text.endswith('";'):
        parts.append(text[:-2])
        return "\n".join(parts), start_idx + 1
    elif text.endswith('"'):
        parts.append(text[:-1])
    else:
        parts.append(text)

    i = start_idx + 1
    while i < len(lines):
        line = lines[i].strip()
        if not line.startswith('"'):
            break
        content = line[1:]
        if content.endswith('";'):
            parts.append(content[:-2])
            i += 1
            break
        elif content.endswith('"'):
            parts.append(content[:-1])
        else:
            parts.append(content)
        i += 1

    return "".join(parts), i


# ═══════════════════════════════════════════════════════════════════════════════
#  SECTION 8c — AUTO-DETECT FILE FORMAT
# ═══════════════════════════════════════════════════════════════════════════════

def parse_input_file(filepath: str) -> list[Routine]:
    ext = Path(filepath).suffix.upper()
    if ext == ".L5X":
        log.info("Detected L5X format (XML)")
        return parse_l5x(filepath)
    elif ext in (".L5K", ".TXT"):
        log.info("Detected L5K format (text)")
        return parse_l5k(filepath)
    else:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            head = f.read(100).strip()
        if head.startswith("<?xml") or head.startswith("<RSLogix"):
            log.info("Sniffed L5X format (XML)")
            return parse_l5x(filepath)
        else:
            log.info("Sniffed L5K format (text)")
            return parse_l5k(filepath)


# ═══════════════════════════════════════════════════════════════════════════════
#  SECTION 8d — PROJECT CONTEXT EXTRACTION
#
#  Extracts UDTs, tag definitions, AOI signatures, and I/O modules from
#  L5K (text) and L5X (XML) files into structured data for a context dump.
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class UDTMember:
    name: str
    datatype: str
    description: str = ""
    dimension: str = ""          # e.g. "10" for arrays

@dataclass
class UDT:
    name: str
    description: str = ""
    family: str = ""
    members: list[UDTMember] = field(default_factory=list)

@dataclass
class TagDef:
    name: str
    datatype: str
    scope: str = ""              # "Controller" or program name
    description: str = ""
    usage: str = ""              # "Input", "Output", "Local", "InOut" (for AOI tags)
    dimension: str = ""
    radix: str = ""
    default_value: str = ""

@dataclass
class AOIDef:
    name: str
    description: str = ""
    family: str = ""
    revision: str = ""
    parameters: list[TagDef] = field(default_factory=list)   # Input/Output/InOut
    local_tags: list[TagDef] = field(default_factory=list)    # Local variables

@dataclass
class ModuleDef:
    name: str
    parent: str = ""
    catalog_number: str = ""
    vendor: str = ""
    product_type: str = ""
    major_rev: str = ""
    minor_rev: str = ""
    description: str = ""
    slot: str = ""

@dataclass
class ProjectContext:
    """Aggregated project metadata extracted from an L5K or L5X file."""
    source_file: str = ""
    controller_name: str = ""
    processor_type: str = ""
    udts: list[UDT] = field(default_factory=list)
    controller_tags: list[TagDef] = field(default_factory=list)
    program_tags: dict[str, list[TagDef]] = field(default_factory=dict)  # program_name → tags
    aois: list[AOIDef] = field(default_factory=list)
    modules: list[ModuleDef] = field(default_factory=list)

    def is_empty(self) -> bool:
        return (not self.udts and not self.controller_tags
                and not self.program_tags and not self.aois and not self.modules)

    def summary_counts(self) -> dict[str, int]:
        prog_tag_count = sum(len(v) for v in self.program_tags.values())
        return {
            "UDTs": len(self.udts),
            "Controller Tags": len(self.controller_tags),
            "Program Tag Scopes": len(self.program_tags),
            "Program Tags (total)": prog_tag_count,
            "Add-On Instructions": len(self.aois),
            "I/O Modules": len(self.modules),
        }


# ── L5K context extraction ──────────────────────────────────────────────────

def _extract_l5k_attr(line: str, key: str) -> str:
    """Extract a quoted attribute value like DESCRIPTION := \"text\" from a line."""
    pattern = rf'{key}\s*:=\s*"([^"]*)"'
    m = re.search(pattern, line, re.IGNORECASE)
    return m.group(1) if m else ""


def _extract_l5k_attr_unquoted(line: str, key: str) -> str:
    """Extract an unquoted attribute like RADIX := Decimal."""
    pattern = rf'{key}\s*:=\s*(\S+?)[\s,;)\]]'
    m = re.search(pattern, line + " ", re.IGNORECASE)
    return m.group(1) if m else ""


def extract_context_l5k(filepath: str) -> ProjectContext:
    """Extract project context (UDTs, tags, AOIs, modules) from an L5K file."""
    with open(filepath, "r", encoding="utf-8-sig") as f:
        raw_lines = f.readlines()
    lines = [ln.rstrip() for ln in raw_lines]

    ctx = ProjectContext(source_file=os.path.basename(filepath))

    # Try to find controller name/type from the header
    for line in lines[:30]:
        s = line.strip()
        m = re.match(r'CONTROLLER\s+(\S+)', s)
        if m:
            ctx.controller_name = m.group(1)
        if "ProcessorType" in s or "PROCESSOR_TYPE" in s:
            val = _extract_l5k_attr(s, "ProcessorType") or _extract_l5k_attr_unquoted(s, "ProcessorType")
            if val:
                ctx.processor_type = val

    i = 0
    current_program = ""

    while i < len(lines):
        stripped = lines[i].strip()

        # ── PROGRAM scope tracking ───────────────────────────────────────
        m = re.match(r"PROGRAM\s+(\S+)", stripped)
        if m:
            current_program = m.group(1)
            i += 1
            continue
        if stripped.startswith("END_PROGRAM"):
            current_program = ""
            i += 1
            continue

        # ── DATATYPE (UDT) ───────────────────────────────────────────────
        m = re.match(r"DATATYPE\s+(\S+)", stripped)
        if m:
            udt_name = m.group(1)
            desc = _extract_l5k_attr(stripped, "Description")
            family = _extract_l5k_attr(stripped, "Family")
            # Collect opening lines (may span multiple lines with attributes)
            full_header = stripped
            if ")" not in stripped and "(" in stripped:
                i += 1
                while i < len(lines):
                    full_header += " " + lines[i].strip()
                    if ")" in lines[i]:
                        i += 1
                        break
                    i += 1
                if not desc:
                    desc = _extract_l5k_attr(full_header, "Description")
                if not family:
                    family = _extract_l5k_attr(full_header, "Family")

            members = []
            while i < len(lines):
                ms = lines[i].strip()
                if ms.startswith("END_DATATYPE"):
                    i += 1
                    break
                # Member line:  Name : DataType[Dim] (Description := "...");
                mm = re.match(r"(\w+)\s*:\s*(\S+?)(?:\[(\d+)\])?\s*(?:\((.+?)\))?\s*;", ms)
                if mm:
                    mem_desc = _extract_l5k_attr(ms, "Description") if mm.group(4) else ""
                    members.append(UDTMember(
                        name=mm.group(1),
                        datatype=mm.group(2),
                        dimension=mm.group(3) or "",
                        description=mem_desc,
                    ))
                i += 1

            ctx.udts.append(UDT(name=udt_name, description=desc, family=family, members=members))
            continue

        # ── TAG block ────────────────────────────────────────────────────
        if stripped == "TAG":
            scope = current_program if current_program else "Controller"
            tag_list = []
            i += 1
            while i < len(lines):
                ts = lines[i].strip()
                if ts.startswith("END_TAG"):
                    i += 1
                    break
                # Tag line: Name : DataType[Dim] (attrs) := value ;
                tm = re.match(r"(\w+)\s*:\s*(\S+?)(?:\[([^\]]+)\])?\s*(?:\((.+?)\))?\s*(?::=\s*(.+?))?\s*;", ts)
                if tm:
                    tag_desc = _extract_l5k_attr(ts, "Description") if tm.group(4) else ""
                    tag_radix = _extract_l5k_attr_unquoted(ts, "RADIX") if tm.group(4) else ""
                    tag_usage = _extract_l5k_attr_unquoted(ts, "Usage") if tm.group(4) else ""
                    tag_list.append(TagDef(
                        name=tm.group(1),
                        datatype=tm.group(2),
                        scope=scope,
                        dimension=tm.group(3) or "",
                        description=tag_desc,
                        radix=tag_radix,
                        usage=tag_usage,
                        default_value=(tm.group(5) or "").strip().rstrip(";").strip(),
                    ))
                i += 1

            if scope == "Controller":
                ctx.controller_tags.extend(tag_list)
            else:
                ctx.program_tags.setdefault(scope, []).extend(tag_list)
            continue

        # ── ADD_ON_INSTRUCTION_DEFINITION (AOI) ─────────────────────────
        m = re.match(r"ADD_ON_INSTRUCTION_DEFINITION\s+(\S+)", stripped)
        if m:
            aoi_name = m.group(1)
            # Collect full header (may span lines)
            full_header = stripped
            if "(" in stripped and ")" not in stripped:
                i += 1
                while i < len(lines):
                    full_header += " " + lines[i].strip()
                    if ")" in lines[i]:
                        i += 1
                        break
                    i += 1
            aoi_desc = _extract_l5k_attr(full_header, "Description")
            aoi_family = _extract_l5k_attr(full_header, "Family")
            aoi_rev = _extract_l5k_attr_unquoted(full_header, "Revision")

            params = []
            locals_ = []

            while i < len(lines):
                as_ = lines[i].strip()
                if as_.startswith("END_ADD_ON_INSTRUCTION_DEFINITION"):
                    i += 1
                    break

                # LOCAL_TAG block inside AOI
                if as_ == "LOCAL_TAG":
                    i += 1
                    while i < len(lines):
                        lt = lines[i].strip()
                        if lt.startswith("END_LOCAL_TAG"):
                            i += 1
                            break
                        lm = re.match(r"(\w+)\s*:\s*(\S+?)(?:\[([^\]]+)\])?\s*(?:\((.+?)\))?\s*;", lt)
                        if lm:
                            t_desc = _extract_l5k_attr(lt, "Description") if lm.group(4) else ""
                            t_usage = _extract_l5k_attr_unquoted(lt, "Usage") if lm.group(4) else ""
                            tag = TagDef(
                                name=lm.group(1),
                                datatype=lm.group(2),
                                scope=aoi_name,
                                dimension=lm.group(3) or "",
                                description=t_desc,
                                usage=t_usage,
                            )
                            if t_usage.lower() in ("input", "output", "inout"):
                                params.append(tag)
                            else:
                                locals_.append(tag)
                        i += 1
                    continue
                i += 1

            ctx.aois.append(AOIDef(
                name=aoi_name, description=aoi_desc, family=aoi_family,
                revision=aoi_rev, parameters=params, local_tags=locals_,
            ))
            continue

        # ── MODULE ───────────────────────────────────────────────────────
        m = re.match(r"MODULE\s+(\S+)", stripped)
        if m:
            mod_name = m.group(1)
            # Collect full module block for attribute extraction
            full_block = stripped
            i += 1
            while i < len(lines):
                bl = lines[i].strip()
                full_block += " " + bl
                if bl.startswith("END_MODULE"):
                    i += 1
                    break
                i += 1

            ctx.modules.append(ModuleDef(
                name=mod_name,
                parent=_extract_l5k_attr_unquoted(full_block, "ParentModule") or _extract_l5k_attr_unquoted(full_block, "PARENT"),
                catalog_number=_extract_l5k_attr(full_block, "CatalogNumber") or _extract_l5k_attr(full_block, "CATALOGNUM"),
                vendor=_extract_l5k_attr_unquoted(full_block, "Vendor"),
                product_type=_extract_l5k_attr_unquoted(full_block, "ProductType"),
                major_rev=_extract_l5k_attr_unquoted(full_block, "Major"),
                minor_rev=_extract_l5k_attr_unquoted(full_block, "Minor"),
                description=_extract_l5k_attr(full_block, "Description"),
                slot=_extract_l5k_attr_unquoted(full_block, "Slot") or _extract_l5k_attr_unquoted(full_block, "SLOT"),
            ))
            continue

        i += 1

    return ctx


# ── L5X (XML) context extraction ────────────────────────────────────────────

def extract_context_l5x(filepath: str) -> ProjectContext:
    """Extract project context from an L5X (XML) file."""
    tree = ET.parse(filepath)
    root = tree.getroot()

    ctx = ProjectContext(source_file=os.path.basename(filepath))

    # Controller info
    ctrl = root.find(".//Controller")
    if ctrl is not None:
        ctx.controller_name = ctrl.get("Name", "")
        ctx.processor_type = ctrl.get("ProcessorType", "")

    # ── UDTs ─────────────────────────────────────────────────────────────
    for dt in root.iter("DataType"):
        members = []
        for mem in dt.findall(".//Member"):
            if mem.get("Hidden", "false").lower() == "true":
                continue
            dim = mem.get("Dimension", "")
            desc_el = mem.find("Description")
            members.append(UDTMember(
                name=mem.get("Name", ""),
                datatype=mem.get("DataType", ""),
                dimension=dim,
                description=desc_el.text.strip() if desc_el is not None and desc_el.text else "",
            ))
        desc_el = dt.find("Description")
        ctx.udts.append(UDT(
            name=dt.get("Name", ""),
            description=desc_el.text.strip() if desc_el is not None and desc_el.text else "",
            family=dt.get("Family", ""),
            members=members,
        ))

    # ── Tags (Controller scope) ──────────────────────────────────────────
    ctrl_tags_el = root.find(".//Controller/Tags")
    if ctrl_tags_el is not None:
        for tag in ctrl_tags_el.findall("Tag"):
            desc_el = tag.find("Description")
            ctx.controller_tags.append(TagDef(
                name=tag.get("Name", ""),
                datatype=tag.get("DataType", ""),
                scope="Controller",
                description=desc_el.text.strip() if desc_el is not None and desc_el.text else "",
                dimension=tag.get("Dimension", ""),
                radix=tag.get("Radix", ""),
                usage=tag.get("Usage", ""),
            ))

    # ── Tags (Program scope) ────────────────────────────────────────────
    for prog in root.iter("Program"):
        prog_name = prog.get("Name", "Unknown")
        ptags_el = prog.find("Tags")
        if ptags_el is not None:
            tag_list = []
            for tag in ptags_el.findall("Tag"):
                desc_el = tag.find("Description")
                tag_list.append(TagDef(
                    name=tag.get("Name", ""),
                    datatype=tag.get("DataType", ""),
                    scope=prog_name,
                    description=desc_el.text.strip() if desc_el is not None and desc_el.text else "",
                    dimension=tag.get("Dimension", ""),
                    radix=tag.get("Radix", ""),
                    usage=tag.get("Usage", ""),
                ))
            if tag_list:
                ctx.program_tags[prog_name] = tag_list

    # ── AOIs ─────────────────────────────────────────────────────────────
    for aoi in root.iter("AddOnInstructionDefinition"):
        params, locals_ = [], []
        for ltag in aoi.findall(".//LocalTag"):
            desc_el = ltag.find("Description")
            usage = ltag.get("Usage", "")
            td = TagDef(
                name=ltag.get("Name", ""),
                datatype=ltag.get("DataType", ""),
                scope=aoi.get("Name", ""),
                description=desc_el.text.strip() if desc_el is not None and desc_el.text else "",
                dimension=ltag.get("Dimension", ""),
                radix=ltag.get("Radix", ""),
                usage=usage,
            )
            if usage.lower() in ("input", "output", "inout"):
                params.append(td)
            else:
                locals_.append(td)

        # Also check <Parameters> in some L5X versions
        for ptag in aoi.findall(".//Parameter"):
            desc_el = ptag.find("Description")
            usage = ptag.get("Usage", "")
            td = TagDef(
                name=ptag.get("Name", ""),
                datatype=ptag.get("DataType", ""),
                scope=aoi.get("Name", ""),
                description=desc_el.text.strip() if desc_el is not None and desc_el.text else "",
                dimension=ptag.get("Dimension", ""),
                radix=ptag.get("Radix", ""),
                usage=usage,
            )
            if td.name not in [p.name for p in params]:
                params.append(td)

        desc_el = aoi.find("Description")
        ctx.aois.append(AOIDef(
            name=aoi.get("Name", ""),
            description=desc_el.text.strip() if desc_el is not None and desc_el.text else "",
            family=aoi.get("Family", ""),
            revision=aoi.get("Revision", ""),
            parameters=params,
            local_tags=locals_,
        ))

    # ── Modules ──────────────────────────────────────────────────────────
    for mod in root.iter("Module"):
        desc_el = mod.find("Description")
        ctx.modules.append(ModuleDef(
            name=mod.get("Name", ""),
            parent=mod.get("ParentModule", ""),
            catalog_number=mod.get("CatalogNumber", ""),
            vendor=mod.get("Vendor", ""),
            product_type=mod.get("ProductType", ""),
            major_rev=mod.get("Major", ""),
            minor_rev=mod.get("Minor", ""),
            description=desc_el.text.strip() if desc_el is not None and desc_el.text else "",
            slot=mod.get("Slot", ""),
        ))

    return ctx


# ── Auto-detect format for context extraction ───────────────────────────────

def extract_context(filepath: str) -> ProjectContext:
    """Auto-detect file format and extract project context."""
    ext = Path(filepath).suffix.upper()
    if ext == ".L5X":
        return extract_context_l5x(filepath)
    elif ext in (".L5K", ".TXT"):
        return extract_context_l5k(filepath)
    else:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            head = f.read(100).strip()
        if head.startswith("<?xml") or head.startswith("<RSLogix"):
            return extract_context_l5x(filepath)
        else:
            return extract_context_l5k(filepath)


# ── Render context to readable text ─────────────────────────────────────────

def generate_context_text(ctx: ProjectContext) -> str:
    """Render a ProjectContext into a human-readable text dump."""
    SEP = "=" * 80
    SUBSEP = "-" * 80
    lines: list[str] = []

    lines.append(SEP)
    lines.append("PROJECT CONTEXT DUMP")
    lines.append(SEP)
    lines.append(f"Source File      : {ctx.source_file}")
    if ctx.controller_name:
        lines.append(f"Controller       : {ctx.controller_name}")
    if ctx.processor_type:
        lines.append(f"Processor Type   : {ctx.processor_type}")
    lines.append(f"Generated        : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Tool             : l5x_lad2st.py v{__version__}")
    lines.append("")

    counts = ctx.summary_counts()
    lines.append("Summary:")
    for k, v in counts.items():
        lines.append(f"  {k:<25s} {v}")
    lines.append("")

    # ── UDTs ─────────────────────────────────────────────────────────────
    if ctx.udts:
        lines.append(SEP)
        lines.append(f"USER-DEFINED TYPES (UDTs)  —  {len(ctx.udts)} type(s)")
        lines.append(SEP)
        lines.append("")

        for udt in ctx.udts:
            lines.append(SUBSEP)
            lines.append(f"  UDT: {udt.name}")
            if udt.description:
                lines.append(f"  Description: {udt.description}")
            if udt.family:
                lines.append(f"  Family: {udt.family}")
            lines.append("")
            if udt.members:
                lines.append(f"  {'Member':<30s} {'DataType':<20s} {'Dim':<8s} Description")
                lines.append(f"  {'─'*30} {'─'*20} {'─'*8} {'─'*40}")
                for m in udt.members:
                    dim_str = f"[{m.dimension}]" if m.dimension else ""
                    lines.append(f"  {m.name:<30s} {m.datatype + dim_str:<20s} {'':<8s} {m.description}")
            lines.append("")

    # ── Controller Tags ──────────────────────────────────────────────────
    if ctx.controller_tags:
        lines.append(SEP)
        lines.append(f"CONTROLLER-SCOPE TAGS  —  {len(ctx.controller_tags)} tag(s)")
        lines.append(SEP)
        lines.append("")
        lines.append(f"  {'Tag Name':<40s} {'DataType':<20s} {'Description'}")
        lines.append(f"  {'─'*40} {'─'*20} {'─'*50}")
        for t in ctx.controller_tags:
            dim_str = f"[{t.dimension}]" if t.dimension else ""
            dtype_str = t.datatype + dim_str
            lines.append(f"  {t.name:<40s} {dtype_str:<20s} {t.description}")
        lines.append("")

    # ── Program Tags ─────────────────────────────────────────────────────
    if ctx.program_tags:
        lines.append(SEP)
        prog_tag_total = sum(len(v) for v in ctx.program_tags.values())
        lines.append(f"PROGRAM-SCOPE TAGS  —  {prog_tag_total} tag(s) across {len(ctx.program_tags)} program(s)")
        lines.append(SEP)
        lines.append("")

        for prog_name, tags in sorted(ctx.program_tags.items()):
            lines.append(SUBSEP)
            lines.append(f"  Program: {prog_name}  ({len(tags)} tags)")
            lines.append("")
            lines.append(f"  {'Tag Name':<40s} {'DataType':<20s} {'Description'}")
            lines.append(f"  {'─'*40} {'─'*20} {'─'*50}")
            for t in tags:
                dim_str = f"[{t.dimension}]" if t.dimension else ""
                dtype_str = t.datatype + dim_str
                lines.append(f"  {t.name:<40s} {dtype_str:<20s} {t.description}")
            lines.append("")

    # ── AOIs ─────────────────────────────────────────────────────────────
    if ctx.aois:
        lines.append(SEP)
        lines.append(f"ADD-ON INSTRUCTIONS (AOIs)  —  {len(ctx.aois)} AOI(s)")
        lines.append(SEP)
        lines.append("")

        for aoi in ctx.aois:
            lines.append(SUBSEP)
            lines.append(f"  AOI: {aoi.name}")
            if aoi.description:
                lines.append(f"  Description: {aoi.description}")
            if aoi.revision:
                lines.append(f"  Revision: {aoi.revision}")
            if aoi.family:
                lines.append(f"  Family: {aoi.family}")
            lines.append("")

            if aoi.parameters:
                lines.append(f"  Parameters (Input / Output / InOut):")
                lines.append(f"    {'Name':<30s} {'DataType':<20s} {'Usage':<10s} Description")
                lines.append(f"    {'─'*30} {'─'*20} {'─'*10} {'─'*40}")
                for p in aoi.parameters:
                    lines.append(f"    {p.name:<30s} {p.datatype:<20s} {p.usage:<10s} {p.description}")
                lines.append("")

            if aoi.local_tags:
                lines.append(f"  Local Tags:")
                lines.append(f"    {'Name':<30s} {'DataType':<20s} Description")
                lines.append(f"    {'─'*30} {'─'*20} {'─'*40}")
                for lt in aoi.local_tags:
                    lines.append(f"    {lt.name:<30s} {lt.datatype:<20s} {lt.description}")
                lines.append("")

    # ── Modules ──────────────────────────────────────────────────────────
    if ctx.modules:
        lines.append(SEP)
        lines.append(f"I/O MODULES  —  {len(ctx.modules)} module(s)")
        lines.append(SEP)
        lines.append("")
        lines.append(f"  {'Name':<25s} {'Catalog #':<22s} {'Slot':<6s} {'Parent':<20s} Description")
        lines.append(f"  {'─'*25} {'─'*22} {'─'*6} {'─'*20} {'─'*40}")
        for mod in ctx.modules:
            rev_str = ""
            if mod.major_rev:
                rev_str = f" v{mod.major_rev}"
                if mod.minor_rev:
                    rev_str += f".{mod.minor_rev}"
            cat_str = (mod.catalog_number + rev_str) if mod.catalog_number else ""
            lines.append(f"  {mod.name:<25s} {cat_str:<22s} {mod.slot:<6s} {mod.parent:<20s} {mod.description}")
        lines.append("")

    # ── Footer ───────────────────────────────────────────────────────────
    lines.append(SEP)
    lines.append("END OF CONTEXT DUMP")
    lines.append(SEP)

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  SECTION 9 — OUTPUT GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

FILE_HEADER_TEMPLATE = """\
(* ============================================================================ *)
(* STRUCTURED TEXT — Converted from Ladder Logic (L5X/L5K)                     *)
(* Source : {source:<67s} *)
(* Date  : {date:<67s} *)
(* Tool  : l5x_lad2st.py v{version:<58s} *)
(* ============================================================================ *)
(* NOTES:                                                                      *)
(*  - ONS (one-shot) requires R_TRIG / rising-edge implementation in ST.       *)
(*  - TON/TOF/RTO/CTU/CTD calls retain LAD syntax — verify presets.            *)
(*  - MSG, COP, GSV, SSV, string instrs are identical in LAD and ST.           *)
(*  - Items marked REVIEW need manual attention.                               *)
(* ============================================================================ *)
"""

ROUTINE_HEADER = """\

(* ---------------------------------------------------------------------------- *)
(* ROUTINE : {name}
   Program : {program}
   Type    : {rtype} -> ST
   ---------------------------------------------------------------------------- *)
"""


def generate_combined(
    routines: list[Routine],
    filepath: str,
    stats: ConversionStats,
    *,
    strip_nop: bool = False,
    simplify: bool = False,
) -> str:
    out = [FILE_HEADER_TEMPLATE.format(
        source=os.path.basename(filepath),
        date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        version=__version__,
    )]

    for routine in routines:
        if routine.rtype != "RLL":
            continue
        stats.routines += 1
        stats.rungs_total += len(routine.rungs)

        out.append(ROUTINE_HEADER.format(
            name=routine.name,
            program=routine.program,
            rtype=routine.rtype,
        ))

        for rung in routine.rungs:
            result = convert_rung(
                rung.text, rung.number, rung.comment,
                stats, routine.name,
                strip_nop=strip_nop, simplify=simplify,
            )
            if result is not None:
                out.append(f"// ─── Rung {rung.number} ───")
                out.append(result)
                out.append("")

        out.append(f"(* END ROUTINE: {routine.name} *)")

    return "\n".join(out)


def generate_split(
    routines: list[Routine],
    filepath: str,
    outdir: str,
    stats: ConversionStats,
    *,
    strip_nop: bool = False,
    simplify: bool = False,
) -> list[str]:
    os.makedirs(outdir, exist_ok=True)
    paths = []

    for routine in routines:
        if routine.rtype != "RLL":
            continue
        stats.routines += 1
        stats.rungs_total += len(routine.rungs)

        lines = [
            FILE_HEADER_TEMPLATE.format(
                source=os.path.basename(filepath),
                date=datetime.now().strftime("%Y-%m-%d %H:%M"),
                version=__version__,
            ),
            ROUTINE_HEADER.format(
                name=routine.name,
                program=routine.program,
                rtype=routine.rtype,
            ),
        ]

        for rung in routine.rungs:
            result = convert_rung(
                rung.text, rung.number, rung.comment,
                stats, routine.name,
                strip_nop=strip_nop, simplify=simplify,
            )
            if result is not None:
                lines.append(f"// ─── Rung {rung.number} ───")
                lines.append(result)
                lines.append("")

        lines.append(f"(* END ROUTINE: {routine.name} *)")

        outpath = os.path.join(outdir, f"{routine.name}.st")
        with open(outpath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        paths.append(outpath)
        log.info("Wrote %s", outpath)

    return paths


# ═══════════════════════════════════════════════════════════════════════════════
#  SECTION 10 — CLI
# ═══════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="l5x_lad2st",
        description="Convert Allen-Bradley L5X/L5K Ladder Logic (RLL) to Structured Text.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              %(prog)s Export.L5X                          Single combined .st file
              %(prog)s Project.L5K                         Works with L5K too
              %(prog)s Export.L5X -o MyOutput.st           Custom output filename
              %(prog)s Export.L5X --split                  One .st per routine
              %(prog)s Export.L5X --split -o ./st_output   Split into directory
              %(prog)s Export.L5X --list                   List routines only
              %(prog)s Export.L5X --routines Auto_Sequence MainRoutine
              %(prog)s Project.L5K --simplify --strip-nop  Clean, optimized output
              %(prog)s Export.L5X --report report.json     Save conversion report
        """),
    )
    p.add_argument("input", help="Path to .L5X or .L5K file")
    p.add_argument("-o", "--output", default=None,
                   help="Output .st file or directory (with --split)")
    p.add_argument("--split", action="store_true",
                   help="Write one .st file per routine")
    p.add_argument("--routines", nargs="+", metavar="NAME",
                   help="Convert only these routines (by name)")
    p.add_argument("--list", action="store_true",
                   help="List all routines and exit (no conversion)")
    p.add_argument("--strip-nop", action="store_true",
                   help="Omit NOP-only rungs from output")
    p.add_argument("--simplify", action="store_true",
                   help="Simplify always-true patterns like EQU(X,X)")
    p.add_argument("--report", metavar="FILE",
                   help="Write JSON conversion report to FILE")
    p.add_argument("--no-context", action="store_true",
                   help="Skip generating the _context.txt companion file")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Verbose logging")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Suppress all output except errors")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    level = logging.WARNING
    if args.verbose:
        level = logging.DEBUG
    elif not args.quiet:
        level = logging.INFO
    logging.basicConfig(level=level, format="%(levelname)-7s %(message)s")

    input_path = args.input
    if not os.path.isfile(input_path):
        log.error("File not found: %s", input_path)
        sys.exit(1)

    log.info("Parsing %s ...", input_path)
    all_routines = parse_input_file(input_path)
    rll_routines = [r for r in all_routines if r.rtype == "RLL"]
    non_rll = [r for r in all_routines if r.rtype != "RLL"]

    if args.list:
        print(f"\nRoutines in {os.path.basename(input_path)}:\n")
        print(f"  {'Name':<30s} {'Type':<6s} {'Rungs':>5s}")
        print(f"  {'─'*30} {'─'*6} {'─'*5}")
        for r in all_routines:
            marker = "" if r.rtype == "RLL" else " (skip)"
            print(f"  {r.name:<30s} {r.rtype:<6s} {len(r.rungs):>5d}{marker}")
        print(f"\n  {len(rll_routines)} RLL routine(s) convertible, "
              f"{len(non_rll)} non-RLL skipped.\n")
        sys.exit(0)

    if args.routines:
        names = set(args.routines)
        filtered = [r for r in rll_routines if r.name in names]
        missing = names - {r.name for r in filtered}
        if missing:
            log.warning("Routines not found: %s", ", ".join(sorted(missing)))
        rll_routines = filtered

    if not rll_routines:
        log.error("No RLL routines to convert.")
        sys.exit(1)

    log.info("Converting %d RLL routine(s) ...", len(rll_routines))

    stats = ConversionStats()

    if args.split:
        outdir = args.output or _default_outdir(input_path)
        paths = generate_split(
            rll_routines, input_path, outdir, stats,
            strip_nop=args.strip_nop, simplify=args.simplify,
        )
        if not args.quiet:
            print(f"\nWrote {len(paths)} file(s) to {outdir}/")
    else:
        outpath = args.output or _default_outpath(input_path)
        result = generate_combined(
            rll_routines, input_path, stats,
            strip_nop=args.strip_nop, simplify=args.simplify,
        )
        with open(outpath, "w", encoding="utf-8") as f:
            f.write(result)
        if not args.quiet:
            print(f"\nWrote {outpath}")

    if not args.quiet:
        print(f"\n{stats.summary_text()}\n")

    if args.report:
        report = {
            "source": os.path.basename(input_path),
            "date": datetime.now().isoformat(),
            "tool_version": __version__,
            "routines": stats.routines,
            "rungs_total": stats.rungs_total,
            "rungs_converted": stats.rungs_converted,
            "rungs_nop": stats.rungs_nop,
            "parse_errors": stats.parse_errors,
            "conversion_errors": stats.conversion_errors,
            "review_items": stats.review_items,
        }
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        if not args.quiet:
            print(f"Report saved to {args.report}")

    if stats.parse_errors or stats.conversion_errors:
        sys.exit(2)

    # ── Context companion file ───────────────────────────────────────────
    if not args.no_context:
        try:
            ctx = extract_context(input_path)
            if not ctx.is_empty():
                ctx_path = str(Path(input_path).with_name(
                    Path(input_path).stem + "_context.txt"
                ))
                ctx_text = generate_context_text(ctx)
                with open(ctx_path, "w", encoding="utf-8") as f:
                    f.write(ctx_text)
                if not args.quiet:
                    counts = ctx.summary_counts()
                    non_zero = {k: v for k, v in counts.items() if v > 0}
                    detail = ", ".join(f"{v} {k}" for k, v in non_zero.items())
                    print(f"Context file: {ctx_path}")
                    print(f"  Extracted: {detail}")
            else:
                if not args.quiet:
                    print("Context: no UDTs, tags, AOIs, or modules found — skipping context file.")
        except Exception as exc:
            log.warning("Failed to extract context: %s", exc)


def _default_outpath(inpath: str) -> str:
    return str(Path(inpath).with_suffix(".st"))

def _default_outdir(inpath: str) -> str:
    return str(Path(inpath).parent / (Path(inpath).stem + "_ST"))


if __name__ == "__main__":
    main()
