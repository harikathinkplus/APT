-- Schema for Adaptive Practice Tool

-- 1. Students Table (Populated via registration/login)
CREATE TABLE students (
    student_id VARCHAR(255) PRIMARY KEY,
    student_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    streak INT DEFAULT 0,                 -- Stores highest global streak ever achieved
    current_global_streak INT DEFAULT 0,  -- Tracks active (running) global streak
    no_of_tests INT DEFAULT 0
    -- cumulative_report removed: session reports now stored in session_reports table
);

-- Table to track student streaks per individual topic
CREATE TABLE student_topic_streaks (
    student_id VARCHAR(255) REFERENCES students(student_id) ON DELETE CASCADE,
    topic VARCHAR(255) NOT NULL,
    current_streak INT DEFAULT 0,
    highest_streak INT DEFAULT 0,
    PRIMARY KEY (student_id, topic)
);

-- 2. Questions Table (Populated from Excel sheet import)
CREATE TABLE questions (
    question_id VARCHAR(255) PRIMARY KEY,
    question TEXT NOT NULL,
    options JSONB NOT NULL, -- Options stored as a JSON array, e.g., ["Option A", "Option B"]
    correct_answer TEXT NOT NULL,
    topic VARCHAR(255),
    level VARCHAR(50),
    model VARCHAR(255)
);

-- 3. Logs Table (Populated dynamically during student tests, question-by-question)
CREATE TABLE logs (
    log_id SERIAL PRIMARY KEY,
    student_id VARCHAR(255) REFERENCES students(student_id) ON DELETE CASCADE,
    question_id VARCHAR(255) REFERENCES questions(question_id) ON DELETE CASCADE,
    selected_option TEXT,
    correct_wrong VARCHAR(50), -- E.g. 'Correct', 'Wrong', or 'Seen'
    level VARCHAR(50),
    topic VARCHAR(255),
    time_taken_seconds INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. Practice Sessions Table (Tracks ongoing or paused practice sessions)
CREATE TABLE practice_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id VARCHAR(255) REFERENCES students(student_id) ON DELETE CASCADE,
    selected_topics TEXT[] NOT NULL,
    max_unlocked_level INT DEFAULT 1,
    current_level INT DEFAULT 1,
    status VARCHAR(50) DEFAULT 'IN_PROGRESS', -- IN_PROGRESS, ABANDONED, COMPLETED
    session_progress JSONB, -- Stores queue, current_index pointer, and answer history
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. Session Reports Table (Replaces cumulative_report TEXT column — queryable and structured)
CREATE TABLE session_reports (
    report_id SERIAL PRIMARY KEY,
    session_id UUID,   -- No FK: session rows may be cleaned up independently
    student_id VARCHAR(255) REFERENCES students(student_id) ON DELETE CASCADE,
    report JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =====================================================================
-- INDEXES — critical for query performance at scale
-- =====================================================================

-- Logs: most-queried table in the hot path
CREATE INDEX idx_logs_student_id         ON logs(student_id);
CREATE INDEX idx_logs_student_question   ON logs(student_id, question_id);
-- Partial index: speeds up mark-as-seen lookups (WHERE selected_option IS NULL)
CREATE INDEX idx_logs_student_unseen     ON logs(student_id, question_id) WHERE selected_option IS NULL;

-- Sessions: fetched on every session API call
CREATE INDEX idx_sessions_student_status ON practice_sessions(student_id, status);

-- Questions: topic and level filters used in every session start
CREATE INDEX idx_questions_topic         ON questions(topic);
CREATE INDEX idx_questions_topic_level   ON questions(topic, level);

-- Students: leaderboard query (ORDER BY streak DESC LIMIT 10)
CREATE INDEX idx_students_streak         ON students(streak DESC);

-- Session reports: fetch all reports for a given student
CREATE INDEX idx_session_reports_student ON session_reports(student_id);
