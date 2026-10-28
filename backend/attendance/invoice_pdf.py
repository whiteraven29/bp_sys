"""The invoice as a PDF file — what a student's Download gives them (said
28 October 2026). The same sections as the printable page (invoice_print.html):
particulars, invoice details, items, transactions, where to pay."""

from django.utils import timezone

from . import pdf
from .registers import LOGO

NAVY, INK, MUTED, LINE, SOFT = '#123F80', '#1E293B', '#64748B', '#CBD5E1', '#EEF2F7'
LEFT, RIGHT = 42, pdf.A4[0] - 42


def _money(value):
    return f'{value:,.2f}' if value is not None else ''


def _date(value):
    return value.strftime('%d-%m-%Y') if value else '—'


class _Sheet:
    def __init__(self, title):
        self.doc, self.y, self.title = pdf.Document(), 0, title

    def need(self, height):
        """A new page when `height` more would run off this one."""
        if self.y + height > pdf.A4[1] - 50:
            self.doc.new_page()
            self.y = 50
            self.doc.text(LEFT, self.y - 14, self.title, size=8, colour=MUTED)

    def heading(self, text):
        self.need(40)
        self.y += 16
        self.doc.text(LEFT, self.y, text.upper(), size=8.5, font='bold', colour=NAVY)
        self.y += 6
        self.doc.line(LEFT, self.y, RIGHT, self.y, colour=LINE)
        self.y += 4

    def grid(self, rows):
        """Rows of (label, value, label, value)."""
        col = (RIGHT - LEFT) / 4
        for row in rows:
            self.need(20)
            self.doc.rect(LEFT, self.y, col, 18, fill=SOFT)
            self.doc.rect(LEFT + 2 * col, self.y, col, 18, fill=SOFT)
            self.doc.rect(LEFT, self.y, RIGHT - LEFT, 18, stroke=LINE)
            for i, cell in enumerate(row):
                label = i % 2 == 0
                self.doc.text(LEFT + i * col + 6, self.y + 12, cell, size=8.5 if label else 9,
                              font='regular' if label else 'bold', colour=MUTED if label else INK)
            self.y += 18

    def table(self, heads, rows, widths, aligns, foot=None):
        x_at = [LEFT]
        for w in widths:
            x_at.append(x_at[-1] + w * (RIGHT - LEFT))

        def cells(values, y, font, colour):
            for i, value in enumerate(values):
                if aligns[i] == 'right':
                    self.doc.text(x_at[i + 1] - 5, y, value, size=8.5, font=font, colour=colour, align='right')
                elif aligns[i] == 'center':
                    self.doc.text((x_at[i] + x_at[i + 1]) / 2, y, value, size=8.5, font=font, colour=colour,
                                  align='center')
                else:
                    self.doc.text(x_at[i] + 5, y, value, size=8.5, font=font, colour=colour)

        def head():
            self.doc.rect(LEFT, self.y, RIGHT - LEFT, 18, fill=SOFT)
            cells(heads, self.y + 12, 'bold', MUTED)
            self.y += 18

        self.need(40)
        head()
        for row in rows:
            if self.y + 17 > pdf.A4[1] - 50:
                self.need(400)
                head()
            cells(row, self.y + 12, 'regular', INK)
            self.y += 17
            self.doc.line(LEFT, self.y, RIGHT, self.y, colour=SOFT)
        if foot:
            self.need(20)
            cells(foot, self.y + 12, 'bold', INK)
            self.y += 18
        self.doc.line(LEFT, self.y, RIGHT, self.y, colour=LINE)

    def paragraph(self, text, *, size=8.5, colour=MUTED, font='regular'):
        for line in pdf.wrap(text, size, RIGHT - LEFT, font):
            self.need(13)
            self.y += 12
            self.doc.text(LEFT, self.y, line, size=size, colour=colour, font=font)


def build(invoice, *, college, components, itemised, installments, transactions, total, paid, outstanding,
          expires_on, class_level, terms):
    sheet = _Sheet(f'{college.name} · Invoice {invoice.reference}')
    doc = sheet.doc

    # Letterhead: the logo and the college on the left, the invoice on the right.
    try:
        doc.image(pdf.png_image(LOGO), LEFT, 36, 54, 54)
        text_x = LEFT + 64
    except (OSError, ValueError):
        text_x = LEFT
    y = 50
    for line in pdf.wrap(college.name, 13, 250, 'bold'):
        doc.text(text_x, y, line, size=13, font='bold', colour=NAVY)
        y += 15
    contact = [college.short_name]
    if college.po_box:
        contact.append(f'{college.po_box}, {college.town}, {college.country}')
    contact += [value for value in (college.phone, college.email, college.website) if value]
    for line in contact:
        doc.text(text_x, y, line, size=8, colour=MUTED)
        y += 10
    doc.text(RIGHT, 56, 'INVOICE', size=22, font='bold', colour=NAVY, align='right')
    doc.text(RIGHT, 72, f'{invoice.invoice_group or "Student fees"} · {invoice.academic_year.name}', size=8.5,
             colour=MUTED, align='right')
    doc.text(RIGHT, 90, 'Invoice no.', size=8, colour=MUTED, align='right')
    doc.text(RIGHT, 106, invoice.reference, size=13, font='mono', colour=NAVY, align='right')
    sheet.y = max(y, 112) + 6
    doc.line(LEFT, sheet.y, RIGHT, sheet.y, colour=NAVY, thickness=1.6)
    sheet.y += 8

    sheet.heading('A. Student particulars')
    sheet.grid([('Name', invoice.profile.name, 'Registration no.', invoice.profile.nactvet_reg_no),
                ('Class', class_level or '—', 'Academic year', invoice.academic_year.name)])

    sheet.heading('B. Invoice details')
    sheet.grid([
        ('Invoice number', invoice.reference, 'Invoice group', invoice.invoice_group or 'Student fees'),
        ('Amount required', _money(total), 'Currency', 'TZS'),
        ('Instalments', str(installments), 'Payment option',
         f'PARTIAL — {installments} instalments' if installments > 1 else 'EXACT'),
        ('Issued date', _date(invoice.issued_on), 'Expire date', _date(expires_on)),
        ('Amount paid', _money(paid), 'Amount to be paid', _money(outstanding)),
    ])
    if installments > 1:
        sheet.paragraph(f'Instalment due dates are not printed here — this invoice number stays valid for all '
                        f'{installments} of them. Your portal reminds you before each one falls due.')

    letter = 'C'
    if itemised:
        sheet.heading('C. Item(s) details')
        sheet.table(['S/No', 'Item code', 'Description', 'Frequency', 'Instal.', 'Amount (Tshs)'],
                    [(str(n), item['code'] or '—', item['name'][:48], item['frequency'] or '—',
                      str(item['installments']), _money(item['amount'])) for n, item in enumerate(components, 1)],
                    [.07, .13, .40, .14, .09, .17], ['center', 'left', 'left', 'center', 'center', 'right'],
                    foot=('', '', 'Total', '', '', _money(total)))
        letter = 'D'

    sheet.heading(f'{letter}. Invoice transactions')
    sheet.table(['Trans date', 'Receipt', 'Bank reference', 'Credit', 'Debit', 'Balance'],
                [(_date(row['date']), row['receipt'] or '—', row['bank_reference'] or '—', _money(row['credit']),
                  _money(row['debit']), _money(row['balance'])) for row in transactions],
                [.14, .18, .23, .15, .15, .15], ['center', 'left', 'left', 'right', 'right', 'right'])

    sheet.heading('Payment information')
    account = invoice.bank_account
    if account:
        sheet.grid([('Bank', account.bank_name, 'Account no.', account.account_number),
                    ('Account name', account.account_name, 'Method', 'Bank deposit only'),
                    ('Pays for', account.purpose or '—', 'Quote', invoice.reference)])
    else:
        sheet.paragraph(f'No bank account has been set for {invoice.invoice_group or "these charges"} — '
                        'ask the Accounts Office before paying.')
    if terms:
        sheet.heading('Terms')
        for number, term in enumerate(terms, 1):
            sheet.paragraph(f'{number}. {term}')

    sheet.need(30)
    sheet.y += 22
    doc.text(LEFT, sheet.y, f'{college.short_name} · {invoice.reference} · downloaded '
             f'{timezone.localtime():%d %b %Y %H:%M}', size=7.5, colour=MUTED)
    return doc.output(title=f'Invoice {invoice.reference}')
