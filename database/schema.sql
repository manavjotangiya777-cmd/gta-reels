-- SQLite Schema for GTA Reels Automation

CREATE TABLE IF NOT EXISTS creators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT UNIQUE NOT NULL,
    channel_name TEXT NOT NULL,
    permission_proof TEXT NOT NULL,
    approved INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_id TEXT UNIQUE NOT NULL,
    channel_id TEXT NOT NULL,
    title TEXT NOT NULL,
    duration INTEGER NOT NULL,
    processed INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (channel_id) REFERENCES creators(channel_id)
);

CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vod_id INTEGER NOT NULL,
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    score REAL NOT NULL,
    raw_file_path TEXT,
    rendered_file_path TEXT,
    hook_text TEXT,
    commentary_text TEXT,
    title TEXT,
    description TEXT,
    tags TEXT,
    status TEXT DEFAULT 'pending', -- pending, approved, rejected, uploaded, failed
    telegram_message_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (vod_id) REFERENCES vods(id)
);

CREATE TABLE IF NOT EXISTS upload_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL,
    youtube_video_id TEXT NOT NULL,
    youtube_url TEXT NOT NULL,
    privacy_status TEXT NOT NULL,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (clip_id) REFERENCES clips(id)
);
