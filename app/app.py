"""
Task 3: Mini Audio Collection App
Flask app with:
  - Audio recording / upload + name + phone
  - Auto-extraction: duration, sample rate, bitrate, loudness, noise estimate
  - Submission list with play button
"""

import os
import sys
import sqlite3
import json
import math
import tempfile
import subprocess
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory, render_template

# Audio analysis
import numpy as np
import soundfile as sf
import librosa

# ─── path setup ───────────────────────────────────────────────
BASE_DIR     = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DB_PATH      = os.path.join(BASE_DIR, 'database.db')
UPLOAD_DIR   = os.path.join(BASE_DIR, 'uploads')
os.makedirs(os.path.join(BASE_DIR, 'docs'), exist_ok=True)
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), 'templates')
STATIC_DIR   = os.path.join(os.path.dirname(__file__), 'static')

os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB

ALLOWED_EXTENSIONS = {'wav', 'mp3', 'ogg', 'webm', 'm4a', 'flac', 'opus'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ─── DB helpers ───────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_tables():
    conn = get_db()
    conn.executescript("""
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
    """)
    conn.commit()
    conn.close()

ensure_tables()


# ─── Audio analysis ───────────────────────────────────────────

def convert_to_wav(input_path):
    """Convert any audio to WAV using ffmpeg (if available)."""
    out_path = input_path + '_converted.wav'
    try:
        result = subprocess.run(
            ['ffmpeg', '-y', '-i', input_path, '-ar', '44100', '-ac', '1', out_path],
            capture_output=True, timeout=30
        )
        if result.returncode == 0 and os.path.exists(out_path):
            return out_path
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None

def analyze_audio(file_path):
    """
    Extract audio properties:
    - duration_sec
    - sample_rate_khz
    - bitrate_kbps (estimated from file size + duration)
    - loudness_db   (RMS dB)
    - noise_estimate (heuristic: SNR ratio)
    """
    result = {
        'duration_sec': None,
        'sample_rate_khz': None,
        'bitrate_kbps': None,
        'loudness_db': None,
        'noise_estimate': 'unknown',
    }

    # Try soundfile first (fast, handles WAV/FLAC/OGG)
    audio_data = None
    sr = None
    try:
        audio_data, sr = sf.read(file_path, always_2d=False)
        if len(audio_data.shape) > 1:
            audio_data = audio_data.mean(axis=1)  # stereo → mono
    except Exception:
        # Fallback: try ffmpeg conversion then librosa
        converted = convert_to_wav(file_path)
        target = converted if converted else file_path
        try:
            audio_data, sr = librosa.load(target, sr=None, mono=True)
            if converted:
                try: os.remove(converted)
                except: pass
        except Exception as e:
            result['noise_estimate'] = f'analysis_failed: {str(e)}'
            return result

    if audio_data is None or len(audio_data) == 0:
        return result

    duration = len(audio_data) / sr
    result['duration_sec']   = round(duration, 3)
    result['sample_rate_khz'] = round(sr / 1000, 3)

    # Bitrate estimate: file_size_bits / duration
    try:
        file_size_bits = os.path.getsize(file_path) * 8
        if duration > 0:
            result['bitrate_kbps'] = round(file_size_bits / duration / 1000, 2)
    except:
        pass

    # RMS loudness in dB
    try:
        rms = np.sqrt(np.mean(audio_data ** 2))
        if rms > 0:
            result['loudness_db'] = round(20 * math.log10(rms), 2)
        else:
            result['loudness_db'] = -96.0  # silence floor
    except:
        pass

    # Noise estimate — heuristic using spectral flatness
    # Flat spectrum ≈ noise; peaked spectrum ≈ speech/music
    try:
        flatness = librosa.feature.spectral_flatness(y=audio_data.astype(np.float32))
        mean_flatness = float(np.mean(flatness))
        # flatness near 1 = white noise; near 0 = tonal / speech
        if mean_flatness > 0.5:
            label = 'high_noise'
        elif mean_flatness > 0.15:
            label = 'moderate_noise'
        else:
            label = 'clean'
        result['noise_estimate'] = f'{label} (flatness={mean_flatness:.3f})'
    except:
        result['noise_estimate'] = 'analysis_error'

    return result


# ─── Routes ───────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/submissions')
def submissions_page():
    return render_template('submissions.html')

@app.route('/api/submit', methods=['POST'])
def submit_audio():
    name  = request.form.get('name', '').strip()
    phone = request.form.get('phone', '').strip()

    if not name:
        return jsonify({'error': 'Name is required'}), 400

    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400

    file = request.files['audio']
    if file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'webm'
    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')
    safe_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in name)
    filename = f"{safe_name}_{timestamp}.{ext}"
    file_path = os.path.join(UPLOAD_DIR, filename)

    file.save(file_path)

    # Analyze
    props = analyze_audio(file_path)

    conn = get_db()
    cur  = conn.cursor()

    # Upsert person into persons table (phone-based, no email here)
    cur.execute("""
        INSERT INTO persons (name, phone, sources)
        VALUES (?, ?, 'audio_app')
        ON CONFLICT(email) DO NOTHING
    """, (name, phone or None))

    # Insert audio submission
    cur.execute("""
        INSERT INTO audio_submissions
          (person_name, phone, audio_filename, audio_path,
           duration_sec, sample_rate_khz, bitrate_kbps, loudness_db, noise_estimate)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        name, phone or None, filename, file_path,
        props['duration_sec'], props['sample_rate_khz'],
        props['bitrate_kbps'], props['loudness_db'], props['noise_estimate'],
    ))

    conn.commit()
    conn.close()

    return jsonify({
        'message': 'Submission received',
        'filename': filename,
        'properties': props,
    }), 201


@app.route('/api/submissions', methods=['GET'])
def list_submissions():
    conn = get_db()
    rows = conn.execute("""
        SELECT id, person_name, phone, audio_filename,
               duration_sec, sample_rate_khz, bitrate_kbps,
               loudness_db, noise_estimate, submitted_at
        FROM audio_submissions
        ORDER BY submitted_at DESC
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/audio/<filename>')
def serve_audio(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route('/api/persons', methods=['GET'])
def list_persons():
    conn = get_db()
    rows = conn.execute("""
        SELECT id, name, email, phone, city, skills,
               gig_status, projects_completed, sources
        FROM persons ORDER BY name
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


if __name__ == '__main__':
    app.run(debug=True, port=5050)
