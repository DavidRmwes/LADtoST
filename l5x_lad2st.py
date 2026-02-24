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


def _default_outpath(inpath: str) -> str:
    return str(Path(inpath).with_suffix(".st"))

def _default_outdir(inpath: str) -> str:
    return str(Path(inpath).parent / (Path(inpath).stem + "_ST"))


if __name__ == "__main__":
    main()
