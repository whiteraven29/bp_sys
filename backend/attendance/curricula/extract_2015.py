"""Reads the OLD curriculum, Levels 5 and 6, into one JSON file the system
loads beside the 2022 one (manage.py load_curriculum <file>). Run on a machine
that has the documents; the server never sees them, only the JSON.

    python3 -I backend/attendance/curricula/extract_2015.py \
        --docs "<folder holding ASSESMENT PLAN NTA LEVEL 5.doc and … LEVEL 6.doc>" \
        --out backend/attendance/curricula/pst_2015.json

The old curriculum itself (of February 2015) is not on hand, only its
assessment plans (March 2019, "developed … from the curriculum of February
2015"), so everything comes from them (said 11 November 2026):

  * table 1 of each module: the enabling and sub-enabling outcomes (SEOs);
  * table 2: each SEO's related tasks, ticked for CA or SE. The task letters
    are Word's automatic numbering, read from the plan as printed (a PDF made
    from the same .doc by LibreOffice) and counted when the print lacks one;
  * the instructions: each part of CA and SE, how many, its weight, and the
    SEOs and tasks it is drawn from.

The plans give no weekly hours or credits; those stay 0 ("not given"). Nothing
is guessed: what does not reconcile — weights that do not add to 40 and 60, a
task the instructions name that table 2 does not have, a code the plan gets
wrong — goes to the module's `checks` and is printed.

Needs python-docx and LibreOffice (soffice) to read the .doc files.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

NUMBERS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8}
HEAD = re.compile(r'^assessment plan for module\s+\w+\s*:\s*PST\s*(0\d)\s*(\d{3})\s*:?\s*(.+)$', re.I)
# Where the plan's own code is wrong, with the evidence (a person can check it).
CODE_FIXES = {
    'PST05204': ('PST05104', 'The assessment plan heads it PST05204, but it is module four of semester 1 '
                             '(semester 2 starts at PST05207) and its facilitator guide is "PST 05104 '
                             'PHARMACOLOGY AND THERAPEUTICS": loaded as PST05104.'),
}
TOOLS = {'written_test': 'Question paper and Marking guide', 'written_exam': 'Question paper and Marking guide',
         'practical_test': 'Practical checklist', 'practical_exam': 'Practical checklist',
         'assignment': 'Assignment reports', 'supervisor_report': 'Evaluation checklist (log book)',
         'other': 'Field attachment report'}
LABELS = {'written_test': 'Written tests', 'practical_test': 'Practical tests', 'assignment': 'Individual assignments',
          'written_exam': 'Written examination', 'practical_exam': 'Practical examination'}


def norm(text):
    return re.sub(r'\s+', ' ', (text or '').replace('’', "'").replace(' ', ' ')).strip()


def squash(text):
    return re.sub(r'[^a-z0-9]+', '', (text or '').lower())


def blocks(doc):
    for el in doc.element.body.iterchildren():
        if el.tag == qn('w:p'):
            yield 'p', Paragraph(el, doc)
        elif el.tag == qn('w:tbl'):
            yield 't', Table(el, doc)


def ticked(cell):
    return bool(list(cell._tc.iter(qn('w:sym')))) or norm(cell.text) in ('√', '✓', '✔', 'X', 'x', 'v', 'V')


def convert(path, kind, outdir):
    subprocess.run(['soffice', '--headless', '--convert-to', kind, '--outdir', outdir, path],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return os.path.join(outdir, os.path.splitext(os.path.basename(path))[0] + '.' + kind)


def printed_text(pdf):
    """The plan as printed, squashed, to read the task letters from."""
    text = subprocess.run(['pdftotext', pdf, '-'], check=True, capture_output=True, text=True).stdout
    return re.sub(r'\s+', ' ', text.lower())


# ── the modules, in the order the plan gives them ─────────────────────────────

def split(doc):
    modules, current = [], None
    for kind, block in blocks(doc):
        if kind == 'p':
            text = norm(block.text)
            m = HEAD.match(text)
            if m and not re.search(r'\.{5}', text):
                current = {'code': f'PST{m.group(1)}{m.group(2)}', 'name': norm(m.group(3)).rstrip('.'),
                           'tables': [], 'paras': [], 'appendix': False}
                modules.append(current)
            elif current is not None and not current['appendix']:
                if re.match(r'^appendix\b', text, re.I):
                    current['appendix'] = True
                elif text:
                    current['paras'].append(text)
        elif current is not None and not current['appendix']:
            current['tables'].append(block)
    return modules


def unique_cells(row):
    out, seen = [], set()
    for cell in row.cells:
        if id(cell._tc) not in seen:
            seen.add(id(cell._tc))
            out.append(cell)
    return out


def read_outcomes(table, enabling):
    """Table 1: enabling outcome | sub-enabling outcome(s)."""
    seos = []
    for row in table.rows:
        c = unique_cells(row)
        if len(c) < 2:
            continue
        eo_text, seo_text = norm(c[-2].text), c[-1].text
        em = re.match(r'^(\d+)\s*\.\s*(\d+)\.?\s*(.*)$', eo_text)
        if not em:
            continue
        eo = f'{em.group(1)}.{em.group(2)}'
        enabling.setdefault(eo, norm(em.group(3)))
        for part in re.split(r'(?=\b\d+\s*\.\s*\d+\s*\.\s*\d+)', seo_text):
            sm = re.match(r'^\s*(\d+)\s*\.\s*(\d+)\s*\.\s*(\d+)\.?\s*(.*)$', part, re.S)
            if sm:
                code = f'{sm.group(1)}.{sm.group(2)}.{sm.group(3)}'
                if code not in [s['code'] for s in seos]:
                    seos.append({'code': code, 'text': norm(sm.group(4)).rstrip(), 'enabling': eo,
                                 'criteria': '', 'tasks': []})
    return seos


def read_tasks(table, seos, printed, checks, state):
    """Table 2 (which may run on into a second table): each SEO, then its
    tasks with their CA / SE ticks. `state` carries the SEO being read and the
    place reached in the printed plan from one table to the next."""
    header = [norm(c.text).upper() for c in table.rows[0].cells]
    ca_col = se_col = None
    for row in table.rows[:5]:
        texts = [norm(c.text).upper() for c in row.cells]
        if 'CA' in texts and 'SE' in texts:
            ca_col, se_col = texts.index('CA'), texts.index('SE')
            break
    if ca_col is None:
        ca_col, se_col = len(header) - 2, len(header) - 1
    by_code = {s['code']: s for s in seos}
    current, previous, stages = state.get('seo'), None, {}
    for row in table.rows:
        cells = row.cells
        if len(cells) < 2:
            continue
        cell = cells[1]
        # A cell merged down several rows comes back for each of them; the
        # element itself (not id(), which lxml reuses) tells them apart.
        if previous is not None and cell._tc is previous:
            continue
        previous = cell._tc
        text = norm(cell.text)
        code = re.search(r'(\d+)\s*\.\s*(\d+)\s*\.\s*(\d+)', text)
        if code and (re.search(r'sub\s*[-–—]?\s*enabling', text, re.I) or text.startswith(code.group(0))):
            key = f'{code.group(1)}.{code.group(2)}.{code.group(3)}'
            if key not in by_code:
                title = norm(text[code.end():])
                by_code[key] = {'code': key, 'text': title, 'enabling': '.'.join(key.split('.')[:2]),
                                'criteria': '', 'tasks': []}
                seos.append(by_code[key])
                checks.append(f'SEO {key} is in table 2 but not in table 1.')
            current = state['seo'] = by_code[key]
            continue
        if current is None or not text or re.match(r'^(S/N|TASKS|SUB\s*[-–—]?\s*ENABLING)', text.upper()):
            continue
        stage = ('ca' if ticked(cells[ca_col]) else '') + ('se' if ticked(cells[se_col]) else '')
        items = []
        for p in cell.paragraphs:
            t = norm(p.text)
            if not t:
                continue
            numbered = p._p.find('.//' + qn('w:numPr')) is not None
            literal = re.match(r'^([a-z])\s*\)\s*(.*)$', t)
            if literal:
                items.append([literal.group(1), literal.group(2)])
            elif numbered or not items:
                items.append([None, t])
            else:
                items[-1][1] += ' ' + t
        for letter, task in items:
            used = {t['letter'] for t in current['tasks']}
            if letter is None:
                letter, state['pos'] = printed_letter(task, printed, state.get('pos', 0))
                if letter is None or letter in used:
                    letter = next(chr(c) for c in range(ord('a'), ord('z') + 1) if chr(c) not in used)
            if letter in used:
                checks.append(f'SEO {current["code"]}: task {letter} twice in table 2 ("{task[:50]}").')
                continue
            current['tasks'].append({'letter': letter, 'text': task})
            stages[(current['code'], letter)] = stage
    return stages


def printed_letter(task, printed, pos):
    """The letter the printed plan gives this task, and the place reached.
    One letter wherever the task is printed (a practicum lists tasks of other
    modules under their own letters): that one. Several: the next one after the
    last task found. (None, pos) when the print does not show it."""
    words = re.findall(r'[a-z0-9]+', task.lower())[:8]
    if not words:
        return None, pos
    found = list(re.finditer(r'\b([a-z])\s*\)\W*' + r'\W*'.join(map(re.escape, words)), printed))
    if not found:
        return None, pos
    after = [m for m in found if m.start() >= pos]
    if len({m.group(1) for m in found}) == 1:
        m = after[0] if after else found[0]
        return m.group(1), max(pos, m.end())
    near = [m for m in after if m.start() < pos + 8000]
    return (near[0].group(1), near[0].end()) if near else (None, pos)


# ── the instructions: the parts of CA and SE ──────────────────────────────────

def number(word):
    word = word.lower()
    return int(word) if word.isdigit() else NUMBERS.get(word)


def kind_of(text, stage):
    t = text.lower()
    if 'assignment' in t:
        return 'assignment'
    if 'practical' in t:
        return 'practical_exam' if stage == 'se' else 'practical_test'
    if re.search(r'test|written|theory|examination', t):
        return 'written_exam' if stage == 'se' else 'written_test'
    return None


def covers_in(line):
    """'1.1.1 SEO two (3) RT which are; b and e', 'SEO: 3.3.1 Related task a, b and c',
    'from the SEO 1.1.2 RT (d)' → [(seo, [letters])]."""
    out = []
    for m in re.finditer(r'SEO\s*(\d+\.\d+\.\d+)\s*RTs?\s*\(([a-z])\)', line):
        out.append((m.group(1), [m.group(2)]))
    if out:
        return out
    m = re.search(r'(?:SEO\s*:?\s*)?(\d+\.\d+\.\d+)\s*(?:SEOs?\b)?.*?(?:which\s+(?:is|are)|RTs?\s+(?:is|are))'
                  r'\s*[;:,]?\s*(.+)$', line, re.I) \
        or re.search(r'SEO\s*:?\s*(\d+\.\d+\.\d+)\s*Related\s+tasks?\s*[;:,]?\s*(.+)$', line, re.I)
    if m:
        letters = re.findall(r'\b([a-z])\b', m.group(2))
        out.append((m.group(1), sorted(set(letters), key=letters.index)))
    return out


def read_scheme(paras, checks):
    parts, by_kind = [], {}

    def part(stage, kind, weight=None):
        key = (stage, kind)
        if key not in by_kind:
            by_kind[key] = {'stage': stage, 'method': kind, 'label': LABELS.get(kind, kind), 'tool': TOOLS.get(kind, ''),
                            'min_count': None, 'weight': None, 'covers': []}
            parts.append(by_kind[key])
        if weight is not None and by_kind[key]['weight'] is None:
            by_kind[key]['weight'] = weight
        return by_kind[key]

    whole = ' '.join(paras)
    # A practicum: a checklist filled by the supervisors, and the learner's
    # report. The plan may say it twice, and not always alike.
    pairs = []
    for line in paras:
        m = re.search(r'checklists?\b.*?(\d+)\s*%.*?report\b.*?(\d+)\s*%', line, re.I)
        if m and (m.group(1), m.group(2)) not in pairs:
            pairs.append((m.group(1), m.group(2)))
    if pairs and not re.search(r'written tests?\s+\d', whole, re.I):
        whole_pairs = [x for x in pairs if int(x[0]) + int(x[1]) == 100]
        checklist, report = whole_pairs[0] if whole_pairs else pairs[0]
        for other in pairs:
            if other != (checklist, report):
                checks.append(f'The plan also says checklist {other[0]}% and report {other[1]}% '
                              f'({int(other[0]) + int(other[1])}%); loaded {checklist}% and {report}%.')
        if int(checklist) + int(report) != 100:
            checks.append(f'The field parts add to {int(checklist) + int(report)}%, not 100%.')
        return [{'stage': 'field', 'method': 'supervisor_report', 'label': 'Evaluation checklist (supervisors)',
                 'tool': TOOLS['supervisor_report'], 'min_count': 1, 'weight': checklist, 'covers': []},
                {'stage': 'field', 'method': 'other', 'label': 'Field attachment report',
                 'tool': TOOLS['other'], 'min_count': 1, 'weight': report, 'covers': []}]

    stage, current, listing = 'ca', None, None
    for line in paras:
        low = line.lower()
        # "The continuous assessment will consist of:" then "Written tests 30%" …
        if re.search(r'consists? of:?\s*$', low):
            listing = 'se' if 'semester' in low else 'ca'
            continue
        weight = re.match(r'^(.+?)[\s,:]+(\d+(?:\.\d+)?)\s*%\s*,?\s*$', line)
        if listing and weight and len(weight.group(1)) < 70 and not covers_in(line):
            label = weight.group(1)
            if re.search(r'semester exam', label, re.I):
                continue
            kind = kind_of(label, listing)
            if kind:
                part(listing, kind, weight.group(2))['label'] = norm(label).rstrip(',').capitalize()
            continue
        listing = None
        # The headings that say which part follows.
        if re.match(r'^(\d[\d.]*\s*)?(semester examination|end of semester examination)', low) or \
                re.search(r'^(\d[\d.]*\s*)?(end of )?semester examination', low):
            stage, current = 'se', None
        if re.search(r'^\d[\d.]*\s*continuous assessment', low) or re.match(r'^continuous assessments?\s*\(ca\)', low):
            stage = 'ca'
        heading = len(line) < 60 and not re.search(r'\d+\.\d+\.\d+', line)
        if heading and re.search(r'test|assignment|examination', low) and 'format' not in low:
            kind = kind_of(line, stage)
            if kind:
                current = part(stage, kind)
        # "There shall be two written tests;" / "one written examination which will carry 60%"
        for m in re.finditer(r'(?:there\s+(?:shall|will)\s+be|consist of)\s+(\w+)\s*(?:\(\d+\)\s*)?'
                             r'((?:individual|group|written|practical|theory|continuous|end of semester|\s)*)'
                             r'(tests?|assignments?|examinations?)', low):
            n = number(m.group(1))
            kind = kind_of(m.group(2) + ' ' + m.group(3), 'se' if m.group(3).startswith('exam') else stage)
            if n and kind:
                target = part('se' if kind.endswith('_exam') else 'ca', kind)
                target['min_count'] = target['min_count'] or n
                current = target if current is None or current['method'] != kind else current
        for m in re.finditer(r'one\s+(written|theory|practical)\s+examination[^%]*?(\d+)\s*%', low):
            kind = 'practical_exam' if m.group(1) == 'practical' else 'written_exam'
            current = part('se', kind, m.group(2))
            current['min_count'] = 1
        each = re.search(r'(\w+)\s*(?:\(\d+\)\s*)?(?:individual\s+|written\s+|practical\s+)?(tests?|assignments?)'
                         r'[^.]*?each\s+(?:will\s+)?carry(?:ing)?\s+(\d+(?:\.\d+)?)\s*%', low) or \
            re.search(r'(\w+)\s+written\s+continuous\s+(tests)[^.]*?(\d+)\s*%\s*each', low)
        if each and number(each.group(1)):
            kind = kind_of(each.group(2) + (' practical' if 'practical' in low else ''), 'ca')
            target = part('ca', kind)
            target['min_count'] = target['min_count'] or number(each.group(1))
            if target['weight'] is None:
                target['weight'] = f'{number(each.group(1)) * float(each.group(3)):g}'
        if re.search(r'end of semester examinations?\s+(?:will\s+)?(?:contribute|cover)', low):
            w = re.search(r'(\d+)\s*%', line)
            stage = 'se'
            se_parts = [p for p in parts if p['stage'] == 'se']
            if not se_parts:
                current = part('se', 'written_exam', w.group(1) if w else None)
            else:
                if len(se_parts) == 1 and se_parts[0]['weight'] is None and w:
                    se_parts[0]['weight'] = w.group(1)
                if current is None or current['stage'] != 'se':
                    current = se_parts[0]
        if re.search(r'assignment', low) and covers_in(line):
            current = part('ca', 'assignment')
        found = covers_in(line)
        if found:
            if current is None:
                current = next((p for p in parts if p['stage'] == stage), None) or part(stage, kind_of('test', stage))
            for seo, letters in found:
                have = next((c for c in current['covers'] if c['seo'] == seo), None)
                if have is None:
                    current['covers'].append({'seo': seo, 'tasks': letters})
                else:
                    have['tasks'] += [x for x in letters if x not in have['tasks']]
    for p in parts:
        p['min_count'] = p['min_count'] or 1
        if p['weight'] is None:
            checks.append(f'{p["label"]}: the plan gives no weight.')
            p['weight'] = '0'
    for stage, want in (('ca', 40), ('se', 60)):
        total = sum(float(p['weight']) for p in parts if p['stage'] == stage)
        if parts and abs(total - want) > 0.01:
            checks.append(f'{stage.upper()} parts add to {total:g}%, not {want}%.')
    return parts


# ── putting it together ───────────────────────────────────────────────────────

def read_level(path, level, work):
    pdf = convert(path, 'pdf', work)
    docx_path = convert(path, 'docx', work)
    printed = printed_text(pdf)
    doc = docx.Document(docx_path)
    modules, enabling, state = [], {}, {'pos': 0, 'seo': None}
    for raw in split(doc):
        checks = []
        code = raw['code']
        if code in CODE_FIXES:
            code, why = CODE_FIXES[code]
            checks.append(why)
        outcomes_table = next((t for t in raw['tables'] if len(t.columns) <= 3 and any(
            re.search(r'\d+\s*\.\s*\d+\s*\.\s*\d+', norm(unique_cells(r)[-1].text)) for r in t.rows)), None)
        seos = read_outcomes(outcomes_table, enabling) if outcomes_table is not None else []
        stages = {}
        for table in raw['tables']:
            if len(table.columns) >= 7 and table is not outcomes_table:
                stages.update(read_tasks(table, seos, printed, checks, state))
        state['seo'] = None
        scheme = read_scheme(raw['paras'], checks)
        letters = {(o['code'], t['letter']) for o in seos for t in o['tasks']}
        for p in scheme:
            missing = [f'{c["seo"]} {x}' for c in p['covers'] for x in c['tasks'] if (c['seo'], x) not in letters]
            if missing:
                checks.append(f'{p["label"]}: the instructions name tasks table 2 does not have ({", ".join(missing)}).')
        for o in seos:
            if not o['tasks']:
                checks.append(f'SEO {o["code"]} has no tasks in table 2.')
        modules.append({'code': code, 'name': raw['name'], 'level': level, 'semester': int(code[5]),
                        'hours_per_week': {}, 'credits': 0, 'outcomes': seos, 'checks': checks,
                        'assessment': scheme, '_stages': stages})
    return modules, enabling


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--docs', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    result = {'code': 'PST-2015', 'programme': 'PST',
              'name': 'Pharmaceutical Sciences, NTA Levels 5–6 (curriculum of 2015)',
              'note': ('Read from the assessment plans of March 2019, developed from the curriculum of February 2015; '
                       'the curriculum itself was not on hand, so weekly hours and credits are not given and the '
                       'outcomes carry no assessment criteria. For students already in NTA Levels 5 and 6, who finish '
                       'on it (NACTVET BC.220/244/65 of 26 October 2026; Ministry of Health LB.74/262/01B/172 of '
                       '27 October 2026). Each semester: 15 weeks of study and 2 weeks of assessment.'),
              'weeks_of_study': 15, 'weeks_of_assessment': 2, 'enabling': {}, 'modules': []}
    with tempfile.TemporaryDirectory() as work:
        for level in (5, 6):
            found = [p for p in glob.glob(os.path.join(args.docs, f'*LEVEL {level}.doc')) if '~$' not in p]
            if not found:
                sys.exit(f'No assessment plan for NTA Level {level} in {args.docs}')
            modules, enabling = read_level(found[0], level, work)
            for key, text in enabling.items():
                result['enabling'].setdefault(key, text)
            result['modules'] += modules
    for m in result['modules']:
        stages = m.pop('_stages')
        tasks = sum(len(o['tasks']) for o in m['outcomes'])
        ca = sum(1 for s in stages.values() if 'ca' in s)
        print(f'{m["code"]} L{m["level"]} S{m["semester"]} {m["name"][:42]:42} SEOs {len(m["outcomes"]):2} '
              f'tasks {tasks:3} (CA {ca:2}) parts ' +
              ' + '.join(f'{p["stage"]}:{p["method"]} {p["min_count"]}×={p["weight"]}%' for p in m['assessment']))
        for c in m['checks']:
            print('    !', c)
    with open(args.out, 'w') as f:
        json.dump(result, f, indent=1, ensure_ascii=False)
        f.write('\n')


if __name__ == '__main__':
    main()
