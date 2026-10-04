import hashlib
import hmac
import os
import sqlite3
import time
from datetime import datetime, timedelta

from flask import (Flask, jsonify, redirect, render_template, request,
                   session, url_for)

app = Flask(__name__)
app.permanent_session_lifetime = timedelta(days=60)  # stay signed in on the iPad
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')

DB_PATH = os.path.join(os.path.dirname(__file__), 'study_tracker.db')

# Pastel colour per subject. Colours are looked up when data is read,
# so changing a colour here updates old entries too.
SUBJECTS = {
    'Anatomy':      '#FFC4CF',  # rose
    'Biochemistry': '#BFEBD3',  # mint
    'Physiology':   '#FFD9B8',  # peach
    'Pathology':    '#D8CCF5',  # lilac
    'Pharmacology': '#C4E3F7',  # sky
    'Microbiology': '#FFF0B0',  # butter
}
FALLBACK_COLOR = '#E6E1F3'

# Spaced repetition: first review 3 days after studying, then the gaps below
# are counted from the day you actually review. After the last one the topic
# counts as "mastered".
REVIEW_INTERVALS = [3, 7, 14, 30]


# ---------- database ----------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS study_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            subtopic TEXT NOT NULL,
            date_studied TEXT NOT NULL,
            next_review TEXT NOT NULL,
            color TEXT NOT NULL
        )
    ''')
    # Upgrade databases created by the first version of the app.
    existing = {row['name'] for row in conn.execute('PRAGMA table_info(study_logs)')}
    if 'stage' not in existing:
        conn.execute('ALTER TABLE study_logs ADD COLUMN stage INTEGER NOT NULL DEFAULT 0')
    if 'last_reviewed' not in existing:
        conn.execute('ALTER TABLE study_logs ADD COLUMN last_reviewed TEXT')
    conn.commit()
    conn.close()


init_db()


# ---------- helpers ----------
def parse_date(value):
    """Return a datetime for 'YYYY-MM-DD', or None if it is not valid."""
    try:
        return datetime.strptime(value or '', '%Y-%m-%d')
    except ValueError:
        return None


def client_today():
    """Use the browser's local date when sent, so reminders match the user's timezone."""
    sent = parse_date(request.args.get('today') or (request.get_json(silent=True) or {}).get('today'))
    return (sent or datetime.now()).strftime('%Y-%m-%d')


def serialize(row, today):
    stage = row['stage']
    mastered = stage >= len(REVIEW_INTERVALS)
    next_review = '' if mastered else row['next_review']
    due = bool(next_review) and next_review <= today
    days_overdue = 0
    if due:
        days_overdue = (parse_date(today) - parse_date(next_review)).days
    return {
        'id': row['id'],
        'subject': row['subject'],
        'subtopic': row['subtopic'],
        'date_studied': row['date_studied'],
        'next_review': next_review,
        'last_reviewed': row['last_reviewed'],
        'stage': stage,
        'total_stages': len(REVIEW_INTERVALS),
        'mastered': mastered,
        'due': due,
        'days_overdue': days_overdue,
        'color': SUBJECTS.get(row['subject'], FALLBACK_COLOR),
    }


# ---------- password protection ----------
# The only setting is the APP_PASSWORD environment variable.
# If it is missing the site stays locked, so it can never be open by mistake.
def app_password():
    return os.environ.get('APP_PASSWORD', '')


@app.before_request
def require_login():
    if request.endpoint in ('login', 'static'):
        return None
    if not app_password():
        return 'APP_PASSWORD is not set on the server.', 503
    app.secret_key = hashlib.sha256(('study-planner:' + app_password()).encode()).hexdigest()
    if not session.get('ok'):
        if request.path == '/':
            return redirect(url_for('login'))
        return jsonify({'status': 'error', 'message': 'Please sign in again.'}), 401
    return None


@app.route('/login', methods=['GET', 'POST'])
def login():
    if not app_password():
        return 'APP_PASSWORD is not set on the server.', 503
    app.secret_key = hashlib.sha256(('study-planner:' + app_password()).encode()).hexdigest()
    error = None
    if request.method == 'POST':
        attempt = request.form.get('password', '')
        if hmac.compare_digest(attempt.encode(), app_password().encode()):
            session.permanent = True
            session['ok'] = True
            return redirect(url_for('home'))
        time.sleep(1)  # slows down password guessing
        error = 'That password is not right. Try again.'
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ---------- routes ----------
@app.route('/')
def home():
    return render_template('index.html')


@app.route('/add_study', methods=['POST'])
def add_study():
    data = request.get_json(silent=True) or {}
    subject = data.get('subject')
    subtopic = (data.get('subtopic') or '').strip()
    studied = parse_date(data.get('date_studied'))

    if subject not in SUBJECTS:
        return jsonify({'status': 'error', 'message': 'Pick a subject from the list.'}), 400
    if not subtopic or len(subtopic) > 120:
        return jsonify({'status': 'error', 'message': 'Enter a sub-topic (up to 120 characters).'}), 400
    if studied is None:
        return jsonify({'status': 'error', 'message': 'Enter a valid date.'}), 400

    next_review = studied + timedelta(days=REVIEW_INTERVALS[0])

    conn = get_db()
    conn.execute('''
        INSERT INTO study_logs (subject, subtopic, date_studied, next_review, color, stage)
        VALUES (?, ?, ?, ?, ?, 0)
    ''', (subject, subtopic, studied.strftime('%Y-%m-%d'),
          next_review.strftime('%Y-%m-%d'), SUBJECTS[subject]))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})


@app.route('/get_data', methods=['GET'])
def get_data():
    today = client_today()
    conn = get_db()
    rows = conn.execute('SELECT * FROM study_logs ORDER BY date_studied, id').fetchall()
    conn.close()

    study_logs = [serialize(r, today) for r in rows]
    reminders = sorted((l for l in study_logs if l['due']), key=lambda l: l['next_review'])

    return jsonify({
        'study_logs': study_logs,
        'reminders': reminders,
        'subjects': SUBJECTS,
        'intervals': REVIEW_INTERVALS,
    })


@app.route('/review/<int:log_id>', methods=['POST'])
def review(log_id):
    today = client_today()
    conn = get_db()
    row = conn.execute('SELECT * FROM study_logs WHERE id = ?', (log_id,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Topic not found.'}), 404

    stage = row['stage'] + 1
    if stage >= len(REVIEW_INTERVALS):
        next_review = ''  # mastered, nothing left to schedule
    else:
        next_review = (parse_date(today) + timedelta(days=REVIEW_INTERVALS[stage])).strftime('%Y-%m-%d')

    conn.execute(
        'UPDATE study_logs SET stage = ?, next_review = ?, last_reviewed = ? WHERE id = ?',
        (stage, next_review, today, log_id))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success', 'mastered': stage >= len(REVIEW_INTERVALS), 'next_review': next_review})


@app.route('/delete/<int:log_id>', methods=['DELETE'])
def delete(log_id):
    conn = get_db()
    conn.execute('DELETE FROM study_logs WHERE id = ?', (log_id,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'success'})


if __name__ == '__main__':
    # Local testing only. On your Mac the password is "test" unless you set your own.
    os.environ.setdefault('APP_PASSWORD', 'test')
    app.run(debug=True)
