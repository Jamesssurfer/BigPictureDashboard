# scripts/parser.py — Big Picture Market Report
#
# Converts ONE raw narrative report into the structured dict logger.py needs.
# Best-effort extraction for the narrative style shown in Bigpicturereport.txt
# (date header, opening summary paragraph, ### section headings, bullet lists
# of indices / drivers, and a final **Overall tone** line).
#
# Known limitations:
#   1. Index rows are extracted only when they follow the pattern
#      "- **Name**: Closed at VALUE, up/down CHANGE (PCT)."
#      Free-form prose after the colon is kept as notes when present.
#   2. Sections that are pure narrative (Bonds, Commodities, Currencies,
#      Global Markets) are captured as summary text + a few lightly
#      structured fields (yield ranges, oil/gold moves) via regex.
#   3. If no parseable date is found, the story is rejected (logger will
#      surface the failure). We do not invent a date.
#   4. Multiple stories in one file must be separated by a line containing
#      only "===" (same convention as the William logger).

import re
from datetime import datetime, timezone

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _strip_refs(text):
    if not text:
        return ""
    t = text
    t = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1', t)
    t = re.sub(r'\[[\d,\.\s]+\]', '', t)
    t = re.sub(r'\[\s*\]', '', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    if t and not t.endswith((".", "!", "?", '"')):
        t += "."
    return t


def _split_stories(raw_text):
    normalized = raw_text.replace("\r\n", "\n")
    parts = re.split(r'\n===\n|^===\n|\n===$', normalized)
    return [p.strip() for p in parts if p.strip()]


def _find_header_and_date(text):
    # Prefer bold date line: **September 24, 2026**
    m = re.search(r'\*\*(\w+)\s+(\d{1,2}),\s*(\d{4})\*\*', text)
    if m:
        month_name, day, year = m.groups()
        month = MONTHS.get(month_name.lower())
        if month:
            try:
                weekday = datetime(int(year), month, int(day)).strftime("%A")
            except ValueError:
                weekday = "Unknown"
            header = f"{weekday}, {month_name} {day}, {year}'s Report"
            return header, (int(year), month, int(day))

    # Fallback: "On Thursday, September 24, 2026,"
    m = re.search(r'On\s+(\w+),\s+(\w+)\s+(\d{1,2}),\s*(\d{4})', text)
    if m:
        weekday, month_name, day, year = m.groups()
        month = MONTHS.get(month_name.lower())
        if month:
            header = f"{weekday}, {month_name} {day}, {year}'s Report"
            return header, (int(year), month, int(day))

    # Generic date anywhere
    m = re.search(r'(\w+),\s*(\w+)\s+(\d{1,2}),\s*(\d{4})', text)
    if m:
        weekday, month_name, day, year = m.groups()
        month = MONTHS.get(month_name.lower())
        if month:
            header = f"{weekday}, {month_name} {day}, {year}'s Report"
            return header, (int(year), month, int(day))

    return None, None


def _opening_summary(text):
    """First substantial paragraph after the date header."""
    # Drop the leading === / **date** / blank lines
    body = re.sub(r'^===?\s*', '', text)
    body = re.sub(r'^\*\*[^*]+\*\*\s*', '', body)
    body = body.strip()
    # Take text up to the first ### heading
    m = re.search(r'\n###\s+', body)
    if m:
        body = body[:m.start()]
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip()]
    if paragraphs:
        return _strip_refs(paragraphs[0])
    return ""


def _headline_from_summary(summary):
    if not summary:
        return ""
    # First sentence or first ~120 chars
    m = re.match(r'^([^.!?]+[.!?])', summary)
    if m and len(m.group(1)) > 30:
        return m.group(1).strip()
    return summary[:120].rstrip() + ("..." if len(summary) > 120 else "")


def _section(text, heading_pattern):
    """Return the body of a ### heading until the next ### or end."""
    m = re.search(heading_pattern, text, re.IGNORECASE)
    if not m:
        return ""
    rest = text[m.end():]
    end_m = re.search(r'\n###\s+', rest)
    return rest[:end_m.start()] if end_m else rest


def _parse_indices(section_text):
    indices = []
    # Match lines like:
    # - **Dow Jones Industrial Average**: Closed at 51,349.98, down 161.61 points (−0.31%).
    # - **S&P 500**: Closed at 7,704.13, down 1.90 points (−0.02%). It was the third...
    for line in section_text.split('\n'):
        line = line.strip()
        if not line.startswith(('-', '*')):
            continue
        content = re.sub(r'^[-*]\s*', '', line)
        m = re.match(
            r'\*\*(.+?)\*\*\s*:\s*(?:Closed at\s+)?([\d,]+\.?\d*)?,?\s*'
            r'(?:(up|down)\s+(?:~)?([\d,]+\.?\d*)\s*(?:points?)?)?,?\s*'
            r'(?:\(([+\-−–]?\d+\.?\d*%)\))?\s*(.*)$',
            content,
            re.IGNORECASE,
        )
        if not m:
            # Fallback: just capture name and the rest as notes
            m2 = re.match(r'\*\*(.+?)\*\*\s*:\s*(.*)$', content)
            if m2:
                rest = m2.group(2).strip()
                # Try to pull a bare pct like "down ~0.11%"
                pct_m = re.search(r'([+\-−–]?\d+\.?\d*%)', rest)
                pct_val = pct_m.group(1).replace('−', '-').replace('–', '-') if pct_m else ""
                indices.append({
                    "name": m2.group(1).strip(),
                    "value": "",
                    "change": "",
                    "pct": pct_val,
                    "notes": _strip_refs(rest),
                })
            continue
        name, value, direction, pts, pct, notes = m.groups()
        change = ""
        pct_val = ""
        if pct:
            pct_val = pct.replace('−', '-').replace('–', '-')
        # When the text says "down ~0.11%." (no "points"), treat number as pct
        if direction and pts:
            rest_after = (notes or "") + (pct or "")
            if "point" in content.lower() or re.search(r'points?\b', content, re.I):
                sign = "-" if direction.lower() == "down" else "+"
                change = f"{sign}{pts} points"
            else:
                # bare percentage after up/down
                sign = "-" if direction.lower() == "down" else "+"
                pct_val = f"{sign}{pts}%"
        notes_clean = _strip_refs(notes) if notes else ""
        # Strip leading ". " artifacts
        notes_clean = re.sub(r'^\.\s*', '', notes_clean).strip()
        if notes_clean in (".", "%.", ""):
            notes_clean = ""
        indices.append({
            "name": name.strip(),
            "value": value or "",
            "change": change,
            "pct": pct_val,
            "notes": notes_clean,
        })
    return indices


def _sentence_containing(section_text, keyword):
    """Return the first sentence that contains keyword (handles decimals)."""
    # Split on ". " that is not part of a number (rough heuristic)
    sentences = re.split(r'(?<=[a-zA-Z\)%])\.\s+', section_text)
    for s in sentences:
        if re.search(keyword, s, re.IGNORECASE):
            s = s.strip()
            if not s.endswith(('.', '!', '?')):
                s += '.'
            return _strip_refs(s)
    return ""


def _extract_volatility_breadth_sectors(section_text):
    vol = _sentence_containing(section_text, r'\bVIX\b')
    breadth = _sentence_containing(section_text, r'\bBreadth\b')
    sectors = _sentence_containing(section_text, r'\bSectors were\b')
    if not sectors:
        sectors = _sentence_containing(section_text, r'\bSectors\b')

    # Notable movers — keep the whole clause as one or two clean items
    movers = []
    m = re.search(
        r'Notable individual movers included\s+(.+?)(?:\.\s+Weekly|\.\s*$|\n\n)',
        section_text,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        mover_text = m.group(1).strip()
        # Prefer split on "while" / "and" when they introduce a new company
        parts = re.split(r'\s+while\s+|\s+and\s+(?=[A-Z])', mover_text)
        for part in parts:
            part = part.strip().rstrip(',')
            if part:
                movers.append(_strip_refs(part))

    other = []
    jobless = _sentence_containing(section_text, r'Weekly jobless claims')
    if jobless:
        other.append(jobless)

    return {
        "volatility": vol,
        "breadth": breadth,
        "sectors": sectors,
        "notable_movers": movers,
        "other": " ".join(other),
    }


def _parse_bonds(section_text):
    summary = _strip_refs(section_text.strip()[:800])
    yields = []
    # 10-year
    m = re.search(r'10-year[^.]*?(?:around|at|to)\s*([\d.]+(?:\s*[–-]\s*[\d.]+)?%?)', section_text, re.IGNORECASE)
    if m:
        yields.append(f"10-year: {m.group(1)}")
    m = re.search(r'30-year[^.]*?(?:around|at|to|highest since \d{4})\s*(?:around\s*)?([\d.]+(?:\s*[–-]\s*[\d.]+)?%?)', section_text, re.IGNORECASE)
    if m:
        yields.append(f"30-year: {m.group(1)}")
    return {
        "summary": summary,
        "yields": yields,
        "notes": "",
    }


def _parse_commodities(section_text):
    summary = _strip_refs(section_text.strip()[:600])
    oil_parts = []
    for kw in (r'\bWTI\b', r'\bBrent\b'):
        s = _sentence_containing(section_text, kw)
        if s and s not in oil_parts:
            oil_parts.append(s)
    gold = _sentence_containing(section_text, r'\bGold\b')
    return {
        "summary": summary,
        "oil": " ".join(oil_parts),
        "gold": gold,
    }


def _parse_currencies(section_text):
    return {
        "summary": _strip_refs(section_text.strip()[:500]),
    }


def _parse_global(section_text):
    asia = ""
    europe = ""
    m = re.search(r'\*\*Asia\*\*\s*:?\s*(.+?)(?=\n\s*-\s*\*\*Europe|\n\n|\Z)', section_text, re.IGNORECASE | re.DOTALL)
    if m:
        asia = _strip_refs(m.group(1))
    m = re.search(r'\*\*Europe\*\*\s*:?\s*(.+?)(?=\n\n|\Z)', section_text, re.IGNORECASE | re.DOTALL)
    if m:
        europe = _strip_refs(m.group(1))
    # Fallback if no bold labels
    if not asia and not europe:
        return {"asia": "", "europe": "", "summary": _strip_refs(section_text.strip()[:600])}
    return {"asia": asia, "europe": europe, "summary": ""}


def _parse_key_drivers(section_text):
    drivers = []
    # Numbered list: 1. **Title** — text
    for m in re.finditer(
        r'(\d+)\.\s*\*\*(.+?)\*\*\s*[—–-]\s*(.+?)(?=\n\s*\d+\.\s*\*\*|\n\n|\Z)',
        section_text,
        re.DOTALL,
    ):
        title = m.group(2).strip()
        text = _strip_refs(m.group(3).strip())
        drivers.append({"title": title, "text": text})
    return drivers


def _overall_tone(text):
    m = re.search(r'\*\*Overall tone\*\*\s*:?\s*(.+?)(?=\n\n|\Z)', text, re.IGNORECASE | re.DOTALL)
    if m:
        return _strip_refs(m.group(1))
    m = re.search(r'Overall tone\s*:?\s*(.+?)(?=\n\n|\Z)', text, re.IGNORECASE | re.DOTALL)
    if m:
        return _strip_refs(m.group(1))
    return ""


def parse_story(text):
    header, ymd = _find_header_and_date(text)
    if not header or not ymd:
        raise ValueError("could not find a date or Report header anywhere in this story")

    year, month, day = ymd
    timestamp = f"{year:04d}-{month:02d}-{day:02d}T20:00:00+00:00"

    summary = _opening_summary(text)
    headline = _headline_from_summary(summary)

    us_section = _section(text, r'###\s*U\.?S\.?\s*Equity Markets?\s*\n')
    indices = _parse_indices(us_section)
    extra = _extract_volatility_breadth_sectors(us_section)
    us_equities = {
        "indices": indices,
        "volatility": extra["volatility"],
        "breadth": extra["breadth"],
        "sectors": extra["sectors"],
        "notable_movers": extra["notable_movers"],
        "other": extra["other"],
    }

    bonds_section = _section(text, r'###\s*Bond Market and Interest Rates?\s*\n')
    bonds = _parse_bonds(bonds_section)

    commodities_section = _section(text, r'###\s*Commodities?\s*\n')
    commodities = _parse_commodities(commodities_section)

    currencies_section = _section(text, r'###\s*Currencies and Other Assets?\s*\n')
    currencies = _parse_currencies(currencies_section)

    global_section = _section(text, r'###\s*Global Markets?\s*\n')
    global_markets = _parse_global(global_section)

    drivers_section = _section(text, r'###\s*Key Drivers and Context\s*\n')
    key_drivers = _parse_key_drivers(drivers_section)

    overall_tone = _overall_tone(text)

    return {
        "timestamp": timestamp,
        "header": header,
        "headline": headline,
        "summary": summary,
        "us_equities": us_equities,
        "bonds": bonds,
        "commodities": commodities,
        "currencies": currencies,
        "global_markets": global_markets,
        "key_drivers": key_drivers,
        "overall_tone": overall_tone,
    }


def parse_stories(raw_text):
    events, errors = [], []
    for block in _split_stories(raw_text):
        try:
            events.append(parse_story(block))
        except Exception as e:
            snippet = block.strip().split("\n")[0][:80]
            errors.append((snippet, e))
    return events, errors
