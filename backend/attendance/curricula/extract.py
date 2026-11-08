"""Reads the national curriculum and assessment plan documents into one JSON
file the system loads (manage.py load_curriculum). Run on a machine that has
the documents; the server never sees them, only the JSON.

    python3 -I backend/attendance/curricula/extract.py \
        --docs "<folder holding CURRICUM/ and the ASSESSMENT PLAN folders>" \
        --out backend/attendance/curricula/pst_2022.json

Needs python-docx and poppler's pdftotext. Two sources, each for what it does
well (said 9 November 2026):

  * the curriculum (Word): the modules, their weekly hours (L/T/P/A) and
    credits, the enabling and sub-enabling outcomes (SEOs) and the related
    tasks with their assessment criteria — real tables, read reliably;
  * the assessment plan (PDF): how each module is assessed — CA and SE methods,
    how many, their weights, and the SEOs and tasks each must be drawn from.

Nothing is guessed. What does not reconcile — weights that do not add to 40
and 60, a task the plan names that the curriculum does not have, a module code
the two documents spell differently — is written to the module's `checks` and
printed, for a person to read against the paper.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys

import docx

ROMAN = r'(?:[ivxl]+)'
METHODS = [
    # (pattern on the plan's wording, key, stage hint)
    (r'case\s*study', 'case_study'),
    (r'ospe', 'ospe'),
    (r'oral\s+test', 'oral_test'),
    (r'oral\s+exam', 'oral_exam'),
    (r'practical\s+test', 'practical_test'),
    (r'practical\s+exam', 'practical_exam'),
    (r'written\s+test', 'written_test'),
    (r'written\s+exam', 'written_exam'),
    (r'project', 'project'),
    (r'portfolio', 'portfolio'),
    (r'assignment', 'assignment'),
    (r'tutor', 'tutor_report'),
    (r'supervisor', 'supervisor_report'),
    (r'log\s*book', 'logbook'),
    (r'presentation', 'presentation'),
]
WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6}


def method_key(label, stage):
    text = label.lower()
    for pattern, key in METHODS:
        if re.search(pattern, text):
            # "Practical Examination" inside CA is a practical test of the term.
            if stage == 'ca' and key in ('practical_exam', 'written_exam', 'oral_exam'):
                return key.replace('_exam', '_test')
            if stage == 'se' and key in ('practical_test', 'written_test', 'oral_test'):
                return key.replace('_test', '_exam')
            return key
    return 'other'


def norm(text):
    return re.sub(r'\s+', ' ', (text or '').replace('’', "'")).strip()


def cells(row):
    out, seen = [], set()
    for cell in row.cells:
        if id(cell._tc) in seen:
            continue
        seen.add(id(cell._tc))
        out.append(norm(cell.text))
    return out


def seo_code(text):
    """'1.1. 3 Demonstrate…' → ('1.1.3', 'Demonstrate…')."""
    m = re.match(r'^\s*(\d+)\s*\.\s*(\d+)\s*\.\s*(\d+)\.?\s*(.*)$', text, re.S)
    if not m:
        return None, text
    return f'{m.group(1)}.{m.group(2)}.{m.group(3)}', norm(m.group(4))


# ── the curriculum ────────────────────────────────────────────────────────────

def read_curriculum(path, level):
    doc = docx.Document(path)
    modules, enabling, seos = {}, {}, {}
    order = []
    for table in doc.tables:
        head = ' '.join(cells(table.rows[0])).upper()
        if 'SCHEME OF STUDY' in head:
            semester = 1 if not modules else 2
            for row in table.rows:
                c = cells(row)
                if c and re.match(r'^PST\s*0\d{4}$', c[0].replace(' ', '')):
                    code = c[0].replace(' ', '')
                    nums = [x for x in c[2:] if x != '']
                    def hours(x):
                        return 0 if x in ('-', '', '–') else int(float(x))
                    L, T, P, A = (hours(x) for x in nums[:4])
                    modules[code] = {'code': code, 'name': c[1], 'level': level, 'semester': semester,
                                     'hours_per_week': {'L': L, 'T': T, 'P': P, 'A': A},
                                     'credits': int(nums[4]), 'outcomes': [], 'checks': []}
                    order.append(code)
        elif 'RELATED TASKS' in head:
            # Levels 5 and 6 head this table "Sub-enabling outcomes | Related
            # tasks …", which also reads as the outcomes table: tasks first.
            current = None
            for row in table.rows[1:]:
                c = cells(row)
                if not c:
                    continue
                code, title = seo_code(c[0])
                if code:
                    current = seos.setdefault(code, {'code': code, 'text': title, 'enabling': '', 'tasks': []})
                    if not current['text']:
                        current['text'] = title
                    if len(c) > 2 and c[2]:
                        current['criteria'] = c[2]
                    task_cells = c[1:2]
                else:
                    task_cells = c[:1]
                if current is None:
                    continue
                have = {t['letter'] for t in current['tasks']}
                for cell in task_cells:
                    for m in re.finditer(r'(?:^|\s)([a-z])\s*\)\s*\.?\s*(.+?)(?=(?:\s[a-z]\s*\)\s)|$)', cell, re.S):
                        if m.group(1) not in have:
                            have.add(m.group(1))
                            current['tasks'].append({'letter': m.group(1), 'text': norm(m.group(2))})
        elif 'ENABLING OUTCOMES' in head and 'SUB-ENABLING' in head:
            current = None
            for row in table.rows[1:]:
                c = [x for x in cells(row)]
                if len(c) >= 4 and re.match(r'^\d+\.\d+$', c[0]):
                    current = c[0]
                    enabling[current] = c[1]
                    code, _ = seo_code(c[2])
                    if code:
                        seos[code] = {'code': code, 'text': c[3], 'enabling': current, 'tasks': []}
                elif len(c) >= 2:
                    code, _ = seo_code(c[-2])
                    if code and current:
                        found = seos.get(code)
                        if found is None:
                            seos[code] = {'code': code, 'text': c[-1], 'enabling': current, 'tasks': []}
                        else:
                            found['enabling'] = found['enabling'] or current
    # Which SEOs each module carries: its description in the curriculum.
    paras = [norm(p.text) for p in doc.paragraphs]
    module, reading = None, False
    for text in paras:
        m = re.search(r'Code\s*:\s*(PST\s*0\d{4})', text)
        if m:
            code = m.group(1).replace(' ', '')
            module = modules.get(code)
            if module is None:
                # The description spells it differently from the scheme table
                # (PST04205 for Dispensing, PST04107 for Communication Skills):
                # the scheme table is the authority; match on the number of the
                # module within its level and record the difference.
                tail = code[-2:]
                module = next((mod for c, mod in modules.items() if c[-2:] == tail), None)
                if module is not None:
                    module['checks'].append(f'The curriculum describes it as {code}; its scheme table says {module["code"]}.')
            reading = False
            continue
        if module is None:
            continue
        if re.search(r'Sub-?\s*Enabling Outcomes', text, re.I):
            reading = True
            continue
        if re.search(r'Pre\s*-?requisite', text, re.I):
            reading = False
            continue
        if reading:
            code, title = seo_code(text)
            if code:
                found = seos.get(code)
                module['outcomes'].append({
                    'code': code, 'text': (found or {}).get('text') or title,
                    'enabling': (found or {}).get('enabling', '.'.join(code.split('.')[:2])),
                    'criteria': (found or {}).get('criteria', ''),
                    'tasks': (found or {}).get('tasks', []),
                })
                if found is None:
                    module['checks'].append(f'SEO {code} is listed for the module but has no tasks in the curriculum.')
    for module in modules.values():
        if not module['outcomes'] and not module['code'].endswith(('11', '12', '10')):
            module['checks'].append('No sub-enabling outcomes found in the curriculum.')
    return [modules[c] for c in order], enabling


# ── the assessment plan ───────────────────────────────────────────────────────

def pdf_text(path):
    return subprocess.run(['pdftotext', '-layout', path, '-'], capture_output=True, text=True, check=True).stdout


def split_modules(text):
    """[(code, title, section text)] for each module's part of the plan. The
    plan sometimes gives two modules one code (Level 6: PST06212 for both
    Monitoring of Medicines Use and Pharmacy Practice), so the title is kept
    and the module is matched on both."""
    starts = [(m.start(), m.group(1).replace(' ', ''), norm(m.group(2))) for m in
              re.finditer(r'^\s*(PST\s*0\d{3,4})\s*:?\s+([A-Z][A-Z ,&\-]+)$', text, re.M)]
    parts = []
    for i, (pos, code, title) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        chunk = text[pos:end]
        if re.search(r'4\.1\s*Continuous|Instructions', chunk):
            parts.append((code, title, chunk))
    return parts


def _words(text):
    return {w for w in re.findall(r'[a-z]+', text.lower()) if len(w) > 3}


def find_plan(module, plan):
    """The plan's section for this module: same code and a similar title
    first, then a similar title under another code, then the code alone."""
    name = _words(module['name'])
    def likeness(entry):
        return len(name & _words(entry['title'])) / max(len(name), 1)
    same = [e for e in plan if e['code'] == module['code']]
    best = max(same, key=likeness, default=None)
    if best is not None and likeness(best) >= 0.5:
        return best
    other = max(plan, key=likeness, default=None)
    if other is not None and likeness(other) >= 0.6:
        if other['code'] != module['code']:
            module['checks'].append(f'The assessment plan calls it {other["code"]} ({other["title"].title()}).')
        return other
    return best


COUNT = re.compile(r'^(?:not less than|at least)\s+\w+|^(?:one|two|three|four)$', re.I)
HEADER = re.compile(r'^(S/N|Assessment|Methods?|Number of|Weight|Assessments|Description)\b', re.I)


def _segments(line):
    """[(column, text)]: the words of a line, split where two spaces or more
    part them, with where each piece starts."""
    out = []
    for m in re.finditer(r'\S+(?: \S+)*', line):
        # "2 Assignments": the row number one space from the method's name.
        lead = re.match(r'^(\d)\s+(\D.*)$', m.group(0))
        if lead and not out:
            out.append((m.start(), lead.group(1)))
            out.append((m.start() + m.group(0).index(lead.group(2)), lead.group(2)))
        else:
            out.append((m.start(), m.group(0)))
    return out


def parse_rows(block, stage):
    """The rows of one 'Methods of assessment' table.

    The plan centres each row: a method's name may sit on the line above its
    weight ("Case study / 3 … 10% / scenarios"), and a tool may wrap below. So
    the method column is found first, and a line between two rows goes to the
    next row when it names a method and the next row's own line does not;
    otherwise it continues the row before."""
    lines = []
    for line in block.splitlines():
        if re.match(r'^\s*Total\b', line):
            break
        if not line.strip() or re.fullmatch(r'\s*\d+\s*', line) and not re.search(r'%', line):
            continue
        segs = _segments(line)
        if segs and HEADER.match(segs[0][1]) and not re.search(r'\d\s*%\s*$', line):
            continue
        lines.append(segs)
    # A row's weight; one the plan printed without its "%" (PST05209 "20")
    # counts when the line is a numbered row.
    def is_weight(segs):
        if not segs or len(segs) < 2:
            return False
        last = segs[-1][1]
        if re.fullmatch(r'\d+(?:\.\d+)?\s*%', last):
            return True
        return bool(re.fullmatch(r'\d{1,3}', last) and re.fullmatch(r'\d', segs[0][1]) and int(last) <= 100)
    weighted = [i for i, segs in enumerate(lines) if is_weight(segs)]
    if not weighted:
        return []
    # Where the method column starts: the piece after the row number.
    starts = []
    for i in weighted:
        segs = lines[i]
        body = segs[1:-1] if re.fullmatch(r'\d', segs[0][1]) else segs[:-1]
        if body and not COUNT.match(body[0][1]):
            starts.append(body[0][0])
    method_x = min(starts) if starts else None

    def split(segs):
        label, tool, count, weight = [], [], None, None
        for x, text in segs:
            if re.fullmatch(r'\d+(?:\.\d+)?\s*%', text):
                weight = text.rstrip('% ').strip()
            elif re.fullmatch(r'\d', text) and x < (method_x or 99):
                continue
            elif COUNT.match(text):
                n = re.search(r'(\w+)$', text).group(1).lower()
                count = WORDS.get(n) or (int(n) if n.isdigit() else None)
            elif method_x is not None and method_x - 4 <= x <= method_x + 8:
                label.append(text)
            else:
                tool.append(text)
        return label, tool, count, weight

    rows = []
    for i in weighted:
        label, tool, count, weight = split(lines[i])
        if weight is None and re.fullmatch(r'\d{1,3}', lines[i][-1][1]):
            weight = lines[i][-1][1]
            if weight in tool:
                tool.remove(weight)
            if weight in label:
                label.remove(weight)
        rows.append({'label': label, 'tool': tool, 'min_count': count, 'weight': weight, 'stage': stage, '_at': i})
    for j, segs in enumerate(lines):
        if j in weighted:
            continue
        label, tool, count, _ = split(segs)
        after = next((r for r in rows if r['_at'] > j), None)
        before = next((r for r in reversed(rows) if r['_at'] < j), None)
        target = before or after
        if label and after is not None and not after['label']:
            target = after
        elif before is None:
            target = after
        if j < target['_at']:
            target['label'][:0], target['tool'][:0] = label, tool
        else:
            target['label'] += label
            target['tool'] += tool
        if count and not target['min_count']:
            target['min_count'] = count
    for row in rows:
        row['label'] = norm(' '.join(row['label']))
        row['tool'] = norm(' '.join(row['tool']))
        row['min_count'] = row['min_count'] or 1
        del row['_at']
        row['method'] = method_key(row['label'] if stage != 'field' else row['tool'], stage)
    return rows


def parse_covers(chunk):
    """Under '4.1.1 Written Tests' and so on: which SEOs and tasks each must
    be drawn from. {heading number: (heading, [{'seo', 'tasks'}])}."""
    found = {}
    # "4.1.1 Written Tests" — or "4.1 Written Tests" (Life Skills), but never
    # the "4.1 Continuous Assessments" heading itself.
    for m in re.finditer(r'^\s*(4\.[12](?:\.\d)?)\s*(?!Continuous|Semester|Instructions)([A-Z].*?)$(.*?)'
                         r'(?=^\s*4\.[12](?:\.\d)?\s*[A-Z]|\Z)', chunk, re.M | re.S):
        heading, body = norm(m.group(2)), m.group(3)
        items = []
        for line in body.splitlines():
            line = line.strip()
            sm = re.match(rf'^{ROMAN}\.?\s+(.*)$', line)
            if not sm:
                continue
            rest = sm.group(1)
            code, after = seo_code(re.sub(r'^SEO\s*,?\s*', '', rest))
            if code is None:
                cm = re.search(r'(\d+\.\d+\.\d+)', rest)
                code = cm.group(1) if cm else None
            lm = re.search(r'(?:which\s+(?:is|are)|RTs?\s+(?:is|are))\s*[;:,]?\s*(.+)$', rest, re.I)
            letters = re.findall(r'\b([a-z])\b', lm.group(1)) if lm else []
            letters = [x for x in letters if x not in ('a',) or True]
            items.append({'seo': code, 'tasks': sorted(set(letters), key=letters.index)})
        found[m.group(1)] = (heading, items)
    return found


def read_plan(path):
    text = pdf_text(path)
    plans = {}
    plans = []
    for code, title, chunk in split_modules(text):
        ca = se = []
        cm = re.search(r'4\.1\s*Continuous Assessments?.*?$(.*?)^\s*Total', chunk, re.M | re.S)
        sm = re.search(r'4\.2\s+Semester Examination.*?$(.*?)^\s*Total', chunk, re.M | re.S)
        if cm:
            ca = parse_rows(cm.group(1) + '\n Total', 'ca')
        if sm:
            se = parse_rows(sm.group(1) + '\n Total', 'se')
        field = []
        if not ca and not se:
            fm = re.search(r'Distribution of marks.*?$(.*?)^\s*Total', chunk, re.M | re.S | re.I)
            if fm:
                field = parse_rows(fm.group(1) + '\n Total', 'field')
        covers = parse_covers(chunk)
        plans.append({'code': code, 'title': title, 'ca': ca, 'se': se, 'field': field, 'covers': covers})
    return plans


def attach(module, plan):
    checks = module['checks']
    components = []
    for stage in ('ca', 'se', 'field'):
        for row in plan[stage]:
            components.append({k: row[k] for k in ('stage', 'method', 'label', 'tool', 'min_count', 'weight')} | {'covers': []})
    # Each "4.1.x <Method>" list belongs to the component of that method.
    for number, (heading, items) in plan['covers'].items():
        stage = 'ca' if number.startswith('4.1') else 'se'
        key = method_key(heading, stage)
        target = next((c for c in components if c['stage'] == stage and c['method'] == key), None)
        if target is None:
            # "4.1.4 Practical Tests (OSPE)" against a row "Practical Tests":
            # the same method by its first word.
            first = heading.split()[0].lower()
            target = next((c for c in components if c['stage'] == stage
                           and c['label'].lower().startswith(first)), None)
        if target is None:
            if items:
                checks.append(f'{number} {heading}: no matching method in the weights table.')
            continue
        target['covers'].extend(items)
    # CA is 40% and SE 60% in every module, the new curriculum too: the
    # curriculum's "CA 60% / SE 40%" is the authority's typing error (said 9
    # November 2026). A one-line table that contradicts its own total takes
    # the total; anything else is left as printed and reported.
    for stage, total in (('ca', 40), ('se', 60), ('field', 100)):
        rows = [c for c in components if c['stage'] == stage]
        if rows:
            got = sum(float(c['weight']) for c in rows)
            if abs(got - total) > 0.01:
                if len(rows) == 1:
                    checks.append(f'{rows[0]["label"]} is printed as {got:g}%; the table\'s total and the rule '
                                  f'(CA 40, SE 60) make it {total}%.')
                    rows[0]['weight'] = str(total)
                else:
                    checks.append(f'{stage.upper()} weights add to {got:g}, not {total}.')
    if not components:
        checks.append('No assessment scheme found in the assessment plan.')
    known = {o['code']: {t['letter'] for t in o['tasks']} for o in module['outcomes']}
    for c in components:
        for item in c['covers']:
            if item['seo'] is None:
                checks.append(f'{c["label"]}: a line names no SEO number (tasks {", ".join(item["tasks"])}).')
            elif item['seo'] not in known:
                checks.append(f'{c["label"]}: SEO {item["seo"]} is not one of the module\'s in the curriculum.')
            else:
                missing = [t for t in item['tasks'] if t not in known[item['seo']]]
                if missing:
                    checks.append(f'{c["label"]}: SEO {item["seo"]} has no task {", ".join(missing)} in the curriculum.')
    module['assessment'] = components


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--docs', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    root = args.docs
    curricula = {n: [p for p in glob.glob(os.path.join(root, 'CURRICUM', f'NTA {n}_*.docx')) if '~$' not in os.path.basename(p)][0]
                 for n in (4, 5, 6)}
    plans = {4: glob.glob(os.path.join(root, '*L4*', 'AP*.pdf'))[0],
             5: glob.glob(os.path.join(root, '*L5*', 'AP*.pdf'))[0],
             6: glob.glob(os.path.join(root, '*L6*', 'AP*.pdf'))[0]}
    result = {'code': 'PST-2022', 'programme': 'PST',
              'name': 'Pharmaceutical Sciences, NTA Levels 4–6 (curriculum of 2022)',
              'note': ('Curricula approved January 2022 (edited March 2022, Dodoma); assessment plans of August 2022. '
                       'Each semester: 15 weeks of study and 2 weeks of assessment.'),
              'weeks_of_study': 15, 'weeks_of_assessment': 2,
              'enabling': {}, 'modules': []}
    for level in (4, 5, 6):
        modules, enabling = read_curriculum(curricula[level], level)
        result['enabling'].update(enabling)
        plan = read_plan(plans[level])
        for module in modules:
            match = find_plan(module, plan)
            if match is None:
                module['checks'].append('Not found in the assessment plan.')
                module['assessment'] = []
            else:
                attach(module, match)
            result['modules'].append(module)
    with open(args.out, 'w') as fh:
        json.dump(result, fh, indent=1, ensure_ascii=False)
    for module in result['modules']:
        tasks = sum(len(o['tasks']) for o in module['outcomes'])
        print(f"{module['code']} L{module['level']} S{module['semester']} {module['name'][:40]:40} "
              f"SEOs {len(module['outcomes']):2} tasks {tasks:3} parts {len(module['assessment'])}")
        for check in module['checks']:
            print('    !', check)


if __name__ == '__main__':
    sys.exit(main())
