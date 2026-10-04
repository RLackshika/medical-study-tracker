import os
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# Aesthetic Pastel Color Palette
PASTEL_COLORS = {
    'Anatomy': '#FFB3BA',      # Pastel Soft Red / Rose
    'Biochemistry': '#BAFFC9', # Pastel Soft Green / Mint
    'Physiology': '#FFDFBA'    # Pastel Soft Orange / Peach
}

DB_PATH = os.path.join(os.path.dirname(__file__), 'study_tracker.db')

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS study_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            subtopic TEXT NOT NULL,
            date_studied TEXT NOT NULL,
            next_review TEXT NOT NULL,
            color TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/add_study', methods=['POST'])
def add_study():
    data = request.json
    subject = data.get('subject')
    subtopic = data.get('subtopic')
    date_studied_str = data.get('date_studied')

    date_studied = datetime.strptime(date_studied_str, '%Y-%m-%d')
    next_review = date_studied + timedelta(days=3)
    color = PASTEL_COLORS.get(subject, '#E0C3FC')

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO study_logs (subject, subtopic, date_studied, next_review, color)
        VALUES (?, ?, ?, ?, ?)
    ''', (subject, subtopic, date_studied_str, next_review.strftime('%Y-%m-%d'), color))
    conn.commit()
    conn.close()

    return jsonify({'status': 'success'})

@app.route('/get_data', methods=['GET'])
def get_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT subject, subtopic, date_studied, next_review, color FROM study_logs')
    rows = cursor.fetchall()
    conn.close()

    study_logs = [{
        'subject': r[0],
        'subtopic': r[1],
        'date_studied': r[2],
        'next_review': r[3],
        'color': r[4]
    } for r in rows]

    today_str = datetime.now().strftime('%Y-%m-%d')
    reminders = [log for log in study_logs if log['next_review'] <= today_str]

    return jsonify({
        'study_logs': study_logs,
        'reminders': reminders
    })

if __name__ == '__main__':
    app.run(debug=True)