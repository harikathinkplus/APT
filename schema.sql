-- Schema for Adaptive Practice Tool

-- 1. Students Table (Populated via registration/login)
CREATE TABLE students (
    student_id VARCHAR(255) PRIMARY KEY,
    student_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    streak INT DEFAULT 0,                 -- Original column: stores highest global streak
    current_global_streak INT DEFAULT 0,  -- Added column: tracks active global streak
    no_of_tests INT DEFAULT 0,
    cumulative_report TEXT
);

-- Added table to track student streaks per individual topic
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
    correct_wrong VARCHAR(50), -- E.g. 'Correct' or 'Wrong'
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
    status VARCHAR(50) DEFAULT 'IN_PROGRESS', -- IN_PROGRESS, PAUSED, COMPLETED
    session_progress JSONB, -- Stores active responses & question indices
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
