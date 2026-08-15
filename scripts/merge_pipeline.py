"""
Task 1: Data Merge Pipeline
Merges 3 CSV sources into a single SQLite database.

Matching strategy:
  1. Email match (primary)  — normalized lowercase
  2. Phone match (secondary) — stripped to 10 digits
  3. Name match (fallback)   — normalized title-case
"""

import sqlite3
import pandas as pd
import re
import os
from datetime import datetime

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DB_PATH  = os.path.join(BASE_DIR, 'database.db')
# CSVs live at the workspace root (same level as scripts/)
DATA_DIR = BASE_DIR

# ─────────────────────────────────────────────
# NORMALIZATION HELPERS
# ─────────────────────────────────────────────

def normalize_email(email):
    if pd.isna(email) or str(email).strip() == '':
        return None
    e = str(email).strip().lower()
    # Strip 'alt.' prefix (planted data issue)
    if e.startswith('alt.'):
        e = e[4:]
    return e

def normalize_phone(phone):
    """Strip to 10-digit Indian mobile number."""
    if pd.isna(phone) or str(phone).strip() == '':
        return None
    digits = re.sub(r'\D', '', str(phone))
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith('0'):
        digits = digits[1:]
    return digits if len(digits) == 10 else None

CITY_MAP = {
    'bengaluru': 'Bengaluru', 'bangalore': 'Bengaluru',
    'gurgaon': 'Gurugram',   'gurugram': 'Gurugram',
    'noida': 'Noida',
    'delhi': 'Delhi',        'new delhi': 'Delhi',
    'delhi ncr': 'Delhi NCR',
    'pune': 'Pune',
}

def normalize_city(city):
    if pd.isna(city) or str(city).strip() == '':
        return None
    return CITY_MAP.get(str(city).strip().lower(), str(city).strip().title())

def normalize_name(name):
    if pd.isna(name) or str(name).strip() == '':
        return None
    return str(name).strip().title()

def normalize_status(status):
    if pd.isna(status) or str(status).strip() == '':
        return None
    return str(status).strip().capitalize()

def parse_ctc(ctc):
    """
    CTC values < 100 are in lakhs (data issue), convert to rupees.
    Values >= 100 are already in rupees.
    """
    if pd.isna(ctc):
        return None, None
    try:
        val = float(ctc)
        if val < 100:
            return round(val * 100000, 2), 'converted_from_lakhs'
        return val, None
    except:
        return None, 'parse_failed'

def parse_rate(rate):
    """Normalize '1415/hr', '72k/month' → '1415/hr', '72000/month'."""
    if pd.isna(rate) or str(rate).strip() == '':
        return None
    r = str(rate).strip().lower()
    m_hr    = re.match(r'(\d+)/hr', r)
    m_month = re.match(r'(\d+)(k)?/month', r)
    if m_hr:
        return f"{m_hr.group(1)}/hr"
    if m_month:
        v = int(m_month.group(1)) * (1000 if m_month.group(2) else 1)
        return f"{v}/month"
    return r

DATE_FORMATS = [
    '%Y-%m-%d','%d-%m-%Y','%m/%d/%Y','%d %b %Y',
    '%d/%m/%Y','%d %B %Y','%B %d %Y',
]

def parse_date(d):
    if pd.isna(d) or str(d).strip() == '':
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(str(d).strip(), fmt).strftime('%Y-%m-%d')
        except:
            pass
    return str(d).strip()   # unparseable — return raw

def normalize_verified(v):
    if pd.isna(v):
        return None
    s = str(v).strip().lower()
    return 'Y' if s in ('y','yes','1','true') else ('N' if s in ('n','no','0','false') else None)

def normalize_skills(skills):
    if pd.isna(skills) or str(skills).strip() == '':
        return None
    parts = [s.strip().lower() for s in str(skills).split(',') if s.strip()]
    return ', '.join(sorted(set(parts)))

def merge_skills(a, b):
    parts = set()
    for s in [a, b]:
        if s:
            parts.update(x.strip() for x in s.split(',') if x.strip())
    return ', '.join(sorted(parts)) if parts else None


# ─────────────────────────────────────────────
# DATA QUALITY LOG
# ─────────────────────────────────────────────

issues = []

def log(source, row_id, field, raw, action, note=''):
    issues.append({
        'source': source, 'row_identifier': str(row_id),
        'field': field,   'raw_value': str(raw),
        'action': action, 'note': note,
    })


# ─────────────────────────────────────────────
# LOAD SOURCE 1 — Naukri Applicants
# ─────────────────────────────────────────────

def load_source1():
    df = pd.read_csv(os.path.join(DATA_DIR, 'source1_naukri_applicants.csv'))
    records, seen_emails, seen_phones = [], {}, {}

    for idx, row in df.iterrows():
        raw_email = row['Email']
        raw_phone = str(row['Phone'])
        raw_name  = row['Full Name']

        email = normalize_email(raw_email)
        phone = normalize_phone(raw_phone)
        name  = normalize_name(raw_name)
        city  = normalize_city(row['City'])
        ctc, ctc_note = parse_ctc(row['Current CTC'])
        date  = parse_date(row['Applied Date'])
        skills= normalize_skills(row['Skills'])

        # Issue: abbreviated name (e.g. "R. Verma")
        if re.match(r'^[A-Z]\.\s+', str(raw_name)):
            log('source1', email or name, 'Full Name', raw_name,
                'kept_as_is', 'Abbreviated first name — cannot expand')

        # Issue: 'alt.' prefix on email
        if str(raw_email).strip().lower().startswith('alt.'):
            log('source1', name, 'Email', raw_email,
                'stripped_alt_prefix', 'Alternate-email marker removed for dedup matching')

        # Issue: CTC in lakhs
        if ctc_note == 'converted_from_lakhs':
            log('source1', email or name, 'Current CTC', row['Current CTC'],
                'converted_lakhs_to_rupees', f'Value < 100 treated as lakhs → {ctc}')
        elif ctc_note == 'parse_failed':
            log('source1', email or name, 'Current CTC', row['Current CTC'],
                'set_null', 'Unparseable CTC value')

        # Issue: unparseable date
        if date and date == str(row['Applied Date']).strip():
            log('source1', email or name, 'Applied Date', row['Applied Date'],
                'kept_raw', 'Could not parse into ISO date')

        # Issue: phone missing country code / leading zero
        d = re.sub(r'\D','',raw_phone)
        if len(d) == 10:
            log('source1', email or name, 'Phone', raw_phone,
                'normalized', 'Plain 10-digit number, no country code or leading zero')

        # Intra-source duplicates
        if email:
            if email in seen_emails:
                log('source1', email, 'Email', email, 'flagged_intra_duplicate',
                    f'Same email as row index {seen_emails[email]}')
            else:
                seen_emails[email] = idx
        if phone:
            if phone in seen_phones:
                log('source1', name, 'Phone', phone, 'flagged_intra_duplicate',
                    f'Same phone as row index {seen_phones[phone]}')
            else:
                seen_phones[phone] = idx

        records.append({
            'email': email, 'phone': phone, 'name': name, 'city': city,
            'experience_years': row['Experience (Years)'],
            'current_ctc': ctc, 'applied_date': date, 'skills': skills,
        })

    return pd.DataFrame(records)


# ─────────────────────────────────────────────
# LOAD SOURCE 2 — Gig Workers
# ─────────────────────────────────────────────

def load_source2():
    df = pd.read_csv(os.path.join(DATA_DIR, 'source2_gig_workers.csv'))
    records, seen_emails = [], {}

    for idx, row in df.iterrows():
        raw_email = str(row.get('email_id', '')).strip()

        # Issue: completely blank row
        if not raw_email or raw_email.lower() == 'nan':
            log('source2', f'row_{idx}', 'row', str(row.to_dict()),
                'skipped', 'Blank row')
            continue

        # Issue: shifted/corrupted row — skill_tags ended up in email_id column
        if ',' in raw_email and any(t in raw_email.lower()
                for t in ['react','python','sql','docker','javascript','fastapi']):
            log('source2', f'row_{idx}', 'row', raw_email,
                'skipped', 'Column-shifted row — skills in email_id field')
            continue

        email  = normalize_email(raw_email)
        name   = normalize_name(row.get('worker_name'))
        city   = normalize_city(row.get('location'))
        status = normalize_status(row.get('status'))
        rate   = parse_rate(row.get('rate'))
        skills = normalize_skills(row.get('skill_tags'))

        if email in seen_emails:
            log('source2', email, 'email_id', email,
                'flagged_intra_duplicate',
                f'Duplicate email; earlier occurrence at row {seen_emails[email]}')
        else:
            seen_emails[email] = idx

        records.append({
            'email': email, 'name': name, 'city': city,
            'gig_status': status, 'gig_rate': rate, 'skills': skills,
        })

    return pd.DataFrame(records)


# ─────────────────────────────────────────────
# LOAD SOURCE 3 — CBNexus Contacts
# ─────────────────────────────────────────────

def load_source3():
    df = pd.read_csv(os.path.join(DATA_DIR, 'source3_cbnexus_contacts.csv'))

    # Issue: duplicate header row mid-file
    mask = df['Name'].str.strip().str.lower() == 'name'
    if mask.any():
        log('source3', 'file_level', 'header_row', 'duplicate header',
            'dropped', f'Header repeated at index(es): {list(df[mask].index)}')
        df = df[~mask].reset_index(drop=True)

    records, seen_phones = [], {}

    for idx, row in df.iterrows():
        name  = normalize_name(row['Name'])
        phone = normalize_phone(row['Phone Number'])
        city  = normalize_city(row['City'])
        verified = normalize_verified(row['Verified'])

        try:
            projects = int(row['Projects Completed'])
        except:
            log('source3', name or f'row_{idx}', 'Projects Completed',
                row['Projects Completed'], 'set_null', 'Non-integer value')
            projects = None

        raw_phone = str(row['Phone Number']).strip()
        d = re.sub(r'\D','',raw_phone)
        if len(d) == 12 and d.startswith('91'):
            log('source3', name, 'Phone Number', raw_phone,
                'normalized', 'Removed 91 country prefix')

        if phone:
            if phone in seen_phones:
                log('source3', name, 'Phone Number', phone,
                    'flagged_intra_duplicate', f'Same phone as {seen_phones[phone]}')
            else:
                seen_phones[phone] = name

        records.append({
            'name': name, 'phone': phone, 'city': city,
            'cbnexus_verified': verified, 'projects_completed': projects,
        })

    return pd.DataFrame(records)


# ─────────────────────────────────────────────
# MERGE
# ─────────────────────────────────────────────

def merge_all(df1, df2, df3):
    # Build email index for s1, s2
    s1_by_email = {r['email']: r for r in df1.to_dict('records') if r['email']}
    s2_by_email = {r['email']: r for r in df2.to_dict('records') if r['email']}

    merged = {}  # key = email

    for email in set(list(s1_by_email) + list(s2_by_email)):
        s1 = s1_by_email.get(email, {})
        s2 = s2_by_email.get(email, {})
        sources = []
        if s1: sources.append('source1')
        if s2: sources.append('source2')

        merged[email] = {
            'email': email,
            'name':  s1.get('name') or s2.get('name'),
            'phone': s1.get('phone'),
            'city':  s1.get('city') or s2.get('city'),
            'experience_years': s1.get('experience_years'),
            'current_ctc':  s1.get('current_ctc'),
            'applied_date': s1.get('applied_date'),
            'skills': merge_skills(s1.get('skills'), s2.get('skills')),
            'gig_status': s2.get('gig_status'),
            'gig_rate':   s2.get('gig_rate'),
            'cbnexus_verified':    None,
            'projects_completed':  None,
            'sources': ','.join(sources),
        }

    # Index merged by phone for s3 matching
    merged_by_phone = {}
    for email, rec in merged.items():
        if rec['phone']:
            merged_by_phone[rec['phone']] = email

    # Index merged by name (fallback)
    merged_by_name = {rec['name'].lower(): email
                      for email, rec in merged.items() if rec['name']}

    unmatched_s3 = []

    for row in df3.to_dict('records'):
        phone = row['phone']
        name  = row['name']
        key   = None

        if phone and phone in merged_by_phone:
            key = merged_by_phone[phone]
        elif name and name.lower() in merged_by_name:
            key = merged_by_name[name.lower()]
            log('merge', name, 'match_method', 'name_fallback',
                'matched_by_name', f'Phone not in s1/s2; name matched to {key}')

        if key:
            rec = merged[key]
            rec['cbnexus_verified']   = row['cbnexus_verified']
            rec['projects_completed'] = row['projects_completed']
            if not rec['phone'] and phone:
                rec['phone'] = phone
            if not rec['city'] and row['city']:
                rec['city'] = row['city']
            if 'source3' not in rec['sources']:
                rec['sources'] += ',source3'
        else:
            log('merge', name, 'match', 'unmatched',
                'added_as_new', 'Source3-only record — no email, phone, or name match')
            unmatched_s3.append({
                'email': None, 'name': name, 'phone': phone,
                'city': row['city'], 'experience_years': None,
                'current_ctc': None, 'applied_date': None, 'skills': None,
                'gig_status': None, 'gig_rate': None,
                'cbnexus_verified': row['cbnexus_verified'],
                'projects_completed': row['projects_completed'],
                'sources': 'source3',
            })

    return list(merged.values()) + unmatched_s3


# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    email               TEXT UNIQUE,
    name                TEXT,
    phone               TEXT,
    city                TEXT,
    experience_years    REAL,
    current_ctc         REAL,
    applied_date        TEXT,
    skills              TEXT,
    gig_status          TEXT,
    gig_rate            TEXT,
    cbnexus_verified    TEXT,
    projects_completed  INTEGER,
    sources             TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audio_submissions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    person_name         TEXT NOT NULL,
    phone               TEXT,
    audio_filename      TEXT NOT NULL,
    audio_path          TEXT NOT NULL,
    duration_sec        REAL,
    sample_rate_khz     REAL,
    bitrate_kbps        REAL,
    loudness_db         REAL,
    noise_estimate      TEXT,
    submitted_at        TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS data_issues (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT,
    row_id      TEXT,
    field       TEXT,
    raw_value   TEXT,
    action      TEXT,
    note        TEXT,
    logged_at   TEXT DEFAULT (datetime('now'))
);
"""

def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()

def insert_all(conn, records):
    cur = conn.cursor()
    ok = err = 0
    for r in records:
        try:
            cur.execute("""
                INSERT INTO persons
                  (email,name,phone,city,experience_years,current_ctc,
                   applied_date,skills,gig_status,gig_rate,
                   cbnexus_verified,projects_completed,sources)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                r.get('email'), r.get('name'), r.get('phone'), r.get('city'),
                r.get('experience_years'), r.get('current_ctc'), r.get('applied_date'),
                r.get('skills'), r.get('gig_status'), r.get('gig_rate'),
                r.get('cbnexus_verified'), r.get('projects_completed'), r.get('sources'),
            ))
            ok += 1
        except sqlite3.IntegrityError:
            err += 1
    conn.commit()
    return ok, err

def save_issues(conn):
    cur = conn.cursor()
    for i in issues:
        cur.execute("""
            INSERT INTO data_issues (source,row_id,field,raw_value,action,note)
            VALUES (?,?,?,?,?,?)
        """, (i['source'],i['row_identifier'],i['field'],
              i['raw_value'],i['action'],i['note']))
    conn.commit()


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def run():
    print("=" * 60)
    print("  ConsultBae Data Merge Pipeline")
    print("=" * 60)

    print("\n[1/5] Source 1 — Naukri Applicants")
    df1 = load_source1()
    print(f"      {len(df1)} records cleaned")

    print("\n[2/5] Source 2 — Gig Workers")
    df2 = load_source2()
    print(f"      {len(df2)} records cleaned")

    print("\n[3/5] Source 3 — CBNexus Contacts")
    df3 = load_source3()
    print(f"      {len(df3)} records cleaned")

    print("\n[4/5] Merging...")
    records = merge_all(df1, df2, df3)
    print(f"      {len(records)} unique persons")

    print("\n[5/5] Writing to database...")
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    inserted, dupes = insert_all(conn, records)
    save_issues(conn)
    conn.close()

    print(f"      Inserted: {inserted}  |  Skipped duplicates: {dupes}")
    print(f"      Issues logged: {len(issues)}")
    print(f"\n✓  DB: {os.path.abspath(DB_PATH)}")

    # Save issues CSV
    os.makedirs(os.path.join(BASE_DIR, 'docs'), exist_ok=True)
    pd.DataFrame(issues).to_csv(
        os.path.join(BASE_DIR, 'docs', 'data_issues_report.csv'), index=False)
    print(f"✓  Issues report: docs/data_issues_report.csv")

    print("\n--- Issue breakdown ---")
    df_i = pd.DataFrame(issues)
    if not df_i.empty:
        print(df_i.groupby(['source','action']).size().to_string())


if __name__ == '__main__':
    run()
