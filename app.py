import os
import uuid
import json
import random
import hashlib
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2.pool import ThreadedConnectionPool
from fastapi import FastAPI, Depends, HTTPException, Request, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from typing import List, Optional, Any
import atexit

# Initialize FastAPI application
app = FastAPI(title="Adaptive Practice Tool API")

# Enable Cross-Origin Resource Sharing (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom exception handler to format HTTPExceptions as {"error": "message"}
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )

# Custom exception handler to format RequestValidationError as {"error": "message"}
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    details = exc.errors()
    error_msg = details[0]["msg"] if details else "Validation error"
    field = " -> ".join(str(loc) for loc in details[0]["loc"]) if details else ""
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": f"Validation failed: {error_msg} at {field}"},
    )

# Database connection parameters for PostgreSQL
DB_PARAMS = {
    "host": "localhost",
    "port": 5432,
    "database": "adaptive_practice_db",
    "user": "postgres",
    "password": "Satya@17"
}

# Thread-safe database connection pool (connection load balancer)
db_pool = ThreadedConnectionPool(1, 20, **DB_PARAMS, cursor_factory=RealDictCursor)

# Graceful cleanup of connection pool at app exit
atexit.register(db_pool.closeall)

def hash_password(password: str) -> str:
    # Computes the SHA-256 hash of a password string and returns its hex representation.
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def get_level_rank(level_str):
    # Normalizes a text-based difficulty level string into an integer rank (1 to 5).
    l_str = str(level_str).lower().strip()
    mappings = {
        'very easy': 1, 'veryeasy': 1,
        'easy': 2,
        'moderate': 3,
        'medium': 3,
        'hard': 4,
        'very hard': 5, 'veryhard': 5
    }
    return mappings.get(l_str, 99)

def mark_question_as_seen(student_id, question_id, level, topic, cursor):
    # Utility helper that marks a question as 'Seen' immediately when it is served.
    cursor.execute("""
        SELECT log_id FROM logs 
        WHERE student_id = %s AND question_id = %s AND selected_option IS NULL;
    """, (student_id, question_id))
    
    if not cursor.fetchone():
        query = (
            "INSERT INTO logs (student_id, question_id, correct_wrong, level, topic) "
            "VALUES (%s, %s, 'Seen', %s, %s);"
        )
        cursor.execute(query, (student_id, question_id, level, topic))

# Generator dependency function to manage request-level database connection lifecycle
def get_db():
    conn = db_pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)

# ----------------- BACKGROUND TASKS WORKERS -----------------

def async_log_answer_and_next_seen_worker(student_id, question_id, selected_option, status_str, time_taken, level, topic, next_question):
    conn = db_pool.getconn()
    try:
        cursor = conn.cursor()
        # 1. Update the existing 'Seen' log entry to register the submitted answer
        cursor.execute("""
            UPDATE logs 
            SET selected_option = %s, correct_wrong = %s, time_taken_seconds = %s
            WHERE student_id = %s AND question_id = %s AND selected_option IS NULL;
        """, (selected_option, status_str, time_taken, student_id, question_id))
        
        # Fallback query if no 'Seen' log existed previously
        if cursor.rowcount == 0:
            cursor.execute("""
                INSERT INTO logs (student_id, question_id, selected_option, correct_wrong, level, topic, time_taken_seconds)
                VALUES (%s, %s, %s, %s, %s, %s, %s);
            """, (student_id, question_id, selected_option, status_str, level, topic, time_taken))
            
        # 2. Immediately mark the next served question as 'Seen' in logs
        if next_question:
            next_qid = next_question['question_id']
            cursor.execute("""
                SELECT log_id FROM logs 
                WHERE student_id = %s AND question_id = %s AND selected_option IS NULL;
            """, (student_id, next_qid))
            if not cursor.fetchone():
                cursor.execute("""
                    INSERT INTO logs (student_id, question_id, correct_wrong, level, topic) 
                    VALUES (%s, %s, 'Seen', %s, %s);
                """, (student_id, next_qid, next_question['level'], next_question['topic']))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Async DB log/next-seen task failed: {e}")
    finally:
        db_pool.putconn(conn)

def async_complete_session_worker(session_id, student_id, report):
    conn = db_pool.getconn()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE practice_sessions SET status = 'COMPLETED', updated_at = CURRENT_TIMESTAMP WHERE session_id = %s;", (session_id,))
        cursor.execute("UPDATE students SET no_of_tests = no_of_tests + 1 WHERE student_id = %s;", (student_id,))
        cursor.execute("""
            UPDATE students 
            SET cumulative_report = COALESCE(cumulative_report, '') || '\n' || %s
            WHERE student_id = %s;
        """, (json.dumps(report), student_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Async DB complete session task failed: {e}")
    finally:
        db_pool.putconn(conn)

# ----------------- PYDANTIC SCHEMAS -----------------

class RegisterRequest(BaseModel):
    student_name: str
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class StartSessionRequest(BaseModel):
    student_id: str
    topics: List[str]

class SubmitAnswerRequest(BaseModel):
    session_id: str
    question_id: str
    selected_option: str
    time_taken_seconds: Optional[int] = 0

class ChangeLevelRequest(BaseModel):
    session_id: str
    target_level: Any

class SessionActionRequest(BaseModel):
    session_id: str

# ----------------- AUTH ENDPOINTS -----------------

@app.post('/api/auth/register', status_code=201)
def register(req: RegisterRequest, db=Depends(get_db)):
    """
    API endpoint to register a new student.
    Hashes the student's password and inserts their details into the students table.
    """
    cursor = db.cursor()
    # Check if email already exists
    cursor.execute("SELECT student_id FROM students WHERE email = %s;", (req.email,))
    if cursor.fetchone():
        raise HTTPException(status_code=400, detail="Email already exists")
        
    student_id = str(uuid.uuid4())
    password_hash = hash_password(req.password)
    
    try:
        cursor.execute("""
            INSERT INTO students (student_id, student_name, email, password_hash)
            VALUES (%s, %s, %s, %s)
            RETURNING student_id, student_name, email, streak;
        """, (student_id, req.student_name, req.email, password_hash))
        student = cursor.fetchone()
        return student
    except psycopg2.IntegrityError:
        raise HTTPException(status_code=400, detail="Email or Student ID already exists")

@app.post('/api/auth/login')
def login(req: LoginRequest, db=Depends(get_db)):
    """
    API endpoint to authenticate an existing student.
    Compares the provided password against the hashed password stored in the database.
    """
    cursor = db.cursor()
    cursor.execute("SELECT * FROM students WHERE email = %s;", (req.email,))
    student = cursor.fetchone()
    
    if student and student['password_hash'] == hash_password(req.password):
        student_data = dict(student)
        student_data.pop('password_hash') # Exclude password hash from response payload
        return student_data
        
    raise HTTPException(status_code=401, detail="Invalid email or password")

# ----------------- DASHBOARD ENDPOINTS -----------------

@app.get('/api/topics')
def get_topics(student_id: Optional[str] = None, db=Depends(get_db)):
    """
    API endpoint to fetch all available practice topics in the database.
    If student_id is provided, retrieves the student's highest streak for each topic.
    """
    cursor = db.cursor()
    cursor.execute("SELECT DISTINCT topic FROM questions WHERE topic IS NOT NULL;")
    topics_rows = cursor.fetchall()
    topics = [row['topic'] for row in topics_rows]
    
    topic_streaks = {}
    if student_id:
        cursor.execute("SELECT topic, highest_streak FROM student_topic_streaks WHERE student_id = %s;", (student_id,))
        streak_rows = cursor.fetchall()
        topic_streaks = {row['topic']: row['highest_streak'] for row in streak_rows}
        
    result = []
    for topic in topics:
        result.append({
            "topic": topic,
            "highest_streak": topic_streaks.get(topic, 0)
        })
    return result

@app.get('/api/leaderboard')
def get_leaderboard(db=Depends(get_db)):
    """
    API endpoint to fetch the leaderboard.
    Returns the top 10 students ranked by their highest overall global streak.
    """
    cursor = db.cursor()
    cursor.execute("""
        SELECT student_id, student_name, streak 
        FROM students 
        ORDER BY streak DESC 
        LIMIT 10;
    """)
    return cursor.fetchall()

# ----------------- SESSION ENDPOINTS -----------------

@app.post('/api/session/start')
def start_session(req: StartSessionRequest, db=Depends(get_db)):
    """
    API endpoint to start a new practice session for a student.
    1. Cancels any older active sessions.
    2. Builds difficulty queues (ranks 1 to 5) containing up to 10 random questions per difficulty level.
    3. Excludes previously seen questions (anti-repetition) unless all questions of that topic and level are already seen.
    4. Automatically marks the first served question in the session as 'Seen' in database logs.
    """
    student_id = req.student_id
    selected_topics = req.topics
    
    cursor = db.cursor()
    cursor.execute("SELECT student_id FROM students WHERE student_id = %s;", (student_id,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Student not found")
        
    # Cancel and abandon any active sessions for this student
    cursor.execute("""
        UPDATE practice_sessions 
        SET status = 'ABANDONED' 
        WHERE student_id = %s AND status = 'IN_PROGRESS';
    """, (student_id,))
    
    # Fetch the set of all previously seen/interacted question IDs from student logs (anti-repetition logic)
    cursor.execute("SELECT DISTINCT question_id FROM logs WHERE student_id = %s;", (student_id,))
    answered_rows = cursor.fetchall()
    answered_qids = set(row['question_id'] for row in answered_rows)
    
    # Query all questions belonging to the selected topics
    query = (
        "SELECT question_id, question, options, correct_answer, topic, level, model "
        "FROM questions "
        "WHERE topic = ANY(%s);"
    )
    cursor.execute(query, (selected_topics,))
    all_questions = cursor.fetchall()
    
    if not all_questions:
        raise HTTPException(status_code=404, detail="No questions found for the selected topics")
        
    # Check which topics have models specified
    topics_with_models = set()
    for q in all_questions:
        if q.get('model'):
            topics_with_models.add(q['topic'])
            
    # Group questions by their difficulty rank (1 to 5)
    level_questions = {}
    for q in all_questions:
        lvl = q['level'] or 'Easy'
        lvl_rank = get_level_rank(lvl)
        if lvl_rank == 99:
            continue
        if lvl_rank not in level_questions:
            level_questions[lvl_rank] = []
        level_questions[lvl_rank].append(dict(q))
        
    # Generate the FIFO queues for each difficulty rank
    levels_progress = {}
    for rank in range(1, 6):
        q_pool = level_questions.get(rank, [])
        if not q_pool:
            continue
            
        # Group the questions in this difficulty rank by topic
        by_topic = {}
        for q in q_pool:
            t = q['topic']
            if t not in by_topic:
                by_topic[t] = []
            by_topic[t].append(q)
        
        # Enforce anti-repetition rules and construct candidate lists per topic
        candidates_by_topic = {}
        for t, q_list in by_topic.items():
            unseen = [q for q in q_list if q['question_id'] not in answered_qids]
            seen = [q for q in q_list if q['question_id'] in answered_qids]
            
            # Shuffle subsets to maintain randomness
            random.shuffle(unseen)
            random.shuffle(seen)
            
            if unseen:
                # If there are unseen questions for this topic and level, ONLY allow those. Repetition blocked.
                allowed_for_topic = unseen
            else:
                # Fallback: if all questions for this topic and level are seen, allow them to repeat.
                allowed_for_topic = seen
                
            # Build candidate list for this topic
            if t in topics_with_models:
                # Apply model coverage logic for this topic
                models_in_topic_level = set()
                for q in allowed_for_topic:
                    if q.get('model'):
                        models_in_topic_level.add(q['model'])
                        
                if models_in_topic_level:
                    by_model = {m: [] for m in models_in_topic_level}
                    no_model_qs = []
                    for q in allowed_for_topic:
                        m = q.get('model')
                        if m:
                            by_model[m].append(q)
                        else:
                            no_model_qs.append(q)
                            
                    # Shuffle subsets
                    for m in by_model:
                        random.shuffle(by_model[m])
                    random.shuffle(no_model_qs)
                    
                    topic_candidates = []
                    active_models = list(models_in_topic_level)
                    random.shuffle(active_models)
                    
                    # Round-robin selection by model to cover all models in this topic
                    model_indices = {m: 0 for m in active_models}
                    added_in_round = True
                    while added_in_round:
                        added_in_round = False
                        for m in active_models:
                            idx = model_indices[m]
                            if idx < len(by_model[m]):
                                topic_candidates.append(by_model[m][idx])
                                model_indices[m] += 1
                                added_in_round = True
                                
                    # Append questions without model
                    topic_candidates.extend(no_model_qs)
                    candidates_by_topic[t] = topic_candidates
                else:
                    # No models in this level for this topic, fallback to shuffled list
                    candidates_by_topic[t] = allowed_for_topic
            else:
                # Topic has no models specified at all, select purely based on level (without interference of model)
                candidates_by_topic[t] = allowed_for_topic
                
        # Combine candidates across topics using round-robin topic selection
        selected_qs = []
        active_topics = list(candidates_by_topic.keys())
        # Randomize starting topic
        random.shuffle(active_topics)
        
        topic_indices = {t: 0 for t in active_topics}
        added_in_round = True
        
        while len(selected_qs) < 10 and added_in_round:
            added_in_round = False
            for t in active_topics:
                idx = topic_indices[t]
                if idx < len(candidates_by_topic[t]):
                    selected_qs.append(candidates_by_topic[t][idx])
                    topic_indices[t] += 1
                    added_in_round = True
                    if len(selected_qs) == 10:
                        break
                        
        selected = [q['question_id'] for q in selected_qs]
        
        if not selected:
            continue
            
        # Initialize state structure for the level
        levels_progress[str(rank)] = {
            "queue": selected, # FIFO list of question IDs to be answered
            "answered": {}     # mapping of question_id -> answers submitted during this session
        }
        
    # Verify that we succeeded in populating at least one difficulty level
    sorted_ranks = sorted([int(r) for r in levels_progress.keys()])
    if not sorted_ranks:
        raise HTTPException(status_code=404, detail="No questions available for the selected topics")
        
    session_progress = {
        "levels": levels_progress
    }
    
    # Insert the practice session details into database
    session_id = str(uuid.uuid4())
    initial_level = sorted_ranks[0]
    initial_question_id = levels_progress[str(initial_level)]["queue"][0]
    
    cursor.execute("""
        INSERT INTO practice_sessions (session_id, student_id, selected_topics, current_level, max_unlocked_level, session_progress, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'IN_PROGRESS')
        RETURNING session_id, current_level, max_unlocked_level;
    """, (session_id, student_id, selected_topics, initial_level, initial_level, Json(session_progress)))
    session_row = cursor.fetchone()
    
    # Fetch detailed content of the first question to return to the UI client
    query = (
        "SELECT question_id, question, options, topic, level, model "
        "FROM questions "
        "WHERE question_id = %s;"
    )
    cursor.execute(query, (initial_question_id,))
    active_question = cursor.fetchone()
    
    # Mark the served initial question as 'Seen' in the database logs immediately
    if active_question:
        mark_question_as_seen(student_id, initial_question_id, active_question['level'], active_question['topic'], cursor)
        
    return {
        "session_id": session_row['session_id'],
        "current_level": session_row['current_level'],
        "max_unlocked_level": session_row['max_unlocked_level'],
        "active_question": active_question,
        "queue_length": len(levels_progress[str(initial_level)]["queue"])
    }

@app.post('/api/session/submit-answer')
def submit_answer(req: SubmitAnswerRequest, background_tasks: BackgroundTasks, db=Depends(get_db)):
    """
    API endpoint to submit an answer for the active question.
    1. Validates that the submitted question is indeed at the front of the FIFO queue.
    2. Compares the selected option with correct_answer.
    3. Updates the existing 'Seen' log entry to store the response, correctness, and time taken (Asynchronously).
    4. Updates overall global streak and topic-specific streak (resets to 0 if wrong).
    5. Dequeues the question from the level progress.
    6. If the level queue becomes empty, checks for 100% accuracy to unlock the next level rank.
    7. Serves the next question in the queue (marking it as 'Seen' in background).
    """
    session_id = req.session_id
    question_id = req.question_id
    selected_option = req.selected_option
    time_taken = 0
    
    cursor = db.cursor()
    
    # Retrieve the student's active practice session
    cursor.execute("SELECT * FROM practice_sessions WHERE session_id = %s AND status = 'IN_PROGRESS';", (session_id,))
    session = cursor.fetchone()
    if not session:
        raise HTTPException(status_code=404, detail="Active session not found")
        
    student_id = session['student_id']
    progress = session['session_progress']
    current_level = str(session['current_level'])
    level_progress = progress['levels'][current_level]
    
    # Enforce FIFO Queue order: must answer the question at the front of the queue
    if not level_progress['queue'] or level_progress['queue'][0] != question_id:
        raise HTTPException(status_code=400, detail="Submitted question is not at the front of the queue")
        
    # Fetch question details to retrieve the correct answer and metadata
    cursor.execute("SELECT * FROM questions WHERE question_id = %s;", (question_id,))
    question = cursor.fetchone()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
        
    correct_answer = question['correct_answer']
    topic = question['topic']
    level = question['level']
    
    # Monitor the time taken for this question automatically from server database logs
    cursor.execute("""
        SELECT log_id, created_at FROM logs 
        WHERE student_id = %s AND question_id = %s AND selected_option IS NULL
        ORDER BY created_at DESC LIMIT 1;
    """, (student_id, question_id))
    log_row = cursor.fetchone()
    if log_row:
        cursor.execute("""
            SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - %s))::INT AS elapsed;
        """, (log_row['created_at'],))
        time_taken = cursor.fetchone()['elapsed']
        time_taken = max(1, time_taken if time_taken is not None else 1)
    else:
        time_taken = req.time_taken_seconds or 0
        
    # Verify correctness (case-insensitive and whitespace trimmed)
    is_correct = (str(selected_option).strip().lower() == str(correct_answer).strip().lower())
    status_str = 'Correct' if is_correct else 'Wrong'
    
    # Run streak updates (Streaks are running counts of consecutive correct answers)
    if is_correct:
        # Increment global streak and update students.streak if current_global exceeds peak streak
        cursor.execute("""
            UPDATE students 
            SET 
                current_global_streak = current_global_streak + 1,
                streak = GREATEST(streak, current_global_streak + 1)
            WHERE student_id = %s
            RETURNING current_global_streak, streak;
        """, (student_id,))
        student_streak = cursor.fetchone()
        current_global = student_streak['current_global_streak']
        
        # Increment topic running streak and update highest_streak
        cursor.execute("""
            INSERT INTO student_topic_streaks (student_id, topic, current_streak, highest_streak)
            VALUES (%s, %s, 1, 1)
            ON CONFLICT (student_id, topic) DO UPDATE 
            SET 
                current_streak = student_topic_streaks.current_streak + 1,
                highest_streak = GREATEST(student_topic_streaks.highest_streak, student_topic_streaks.current_streak + 1);
        """, (student_id, topic))
    else:
        # Reset global running streak to 0
        cursor.execute("""
            UPDATE students 
            SET current_global_streak = 0
            WHERE student_id = %s;
        """, (student_id,))
        current_global = 0
        
        # Reset topic running streak to 0
        cursor.execute("""
            INSERT INTO student_topic_streaks (student_id, topic, current_streak, highest_streak)
            VALUES (%s, %s, 0, 0)
            ON CONFLICT (student_id, topic) DO UPDATE 
            SET current_streak = 0;
        """, (student_id, topic))
        
    # Dequeue the answered question from the level's queue list
    level_progress['queue'].pop(0)
    
    # Record response details in the answered dictionary of session state
    level_progress['answered'][question_id] = {
        "selected_option": selected_option,
        "is_correct": is_correct,
        "time_taken_seconds": time_taken
    }
    
    # Evaluate if the active level queue has been completely answered
    level_completed = (len(level_progress['queue']) == 0)
    
    max_unlocked = session['max_unlocked_level']
    accuracy_achieved = False
    
    # Enforce progression block logic upon level completion
    if level_completed:
        # Level is completed. Evaluate if student got 100% accuracy (all responses correct)
        accuracy_achieved = all(ans['is_correct'] is True for ans in level_progress['answered'].values())
        
        if accuracy_achieved:
            # 100% accuracy reached. Unlock the next level rank
            sorted_ranks = sorted([int(r) for r in progress['levels'].keys()])
            try:
                curr_idx = sorted_ranks.index(int(current_level))
                if curr_idx + 1 < len(sorted_ranks):
                    next_rank = sorted_ranks[curr_idx + 1]
                    max_unlocked = max(max_unlocked, next_rank)
            except ValueError:
                pass
                
    # Save session progress and unlock updates to database
    cursor.execute("""
        UPDATE practice_sessions 
        SET session_progress = %s, max_unlocked_level = %s, updated_at = CURRENT_TIMESTAMP
        WHERE session_id = %s;
    """, (Json(progress), max_unlocked, session_id))
    
    # Retrieve the next question from the queue list
    next_question = None
    if not level_completed:
        next_qid = level_progress['queue'][0]
        query = (
            "SELECT question_id, question, options, topic, level, model "
            "FROM questions "
            "WHERE question_id = %s;"
        )
        cursor.execute(query, (next_qid,))
        next_question = cursor.fetchone()
        
    # Dispatch log update and next-seen log insertion asynchronously in the background
    background_tasks.add_task(
        async_log_answer_and_next_seen_worker,
        student_id=student_id,
        question_id=question_id,
        selected_option=selected_option,
        status_str=status_str,
        time_taken=time_taken,
        level=level,
        topic=topic,
        next_question=next_question
    )
    
    return {
        "is_correct": is_correct,
        "correct_answer": correct_answer,
        "current_global_streak": current_global,
        "max_unlocked_level": max_unlocked,
        "level_completed": level_completed,
        "accuracy_achieved": accuracy_achieved,
        "next_question": next_question
    }

@app.post('/api/session/change-level')
def change_level(req: ChangeLevelRequest, db=Depends(get_db)):
    """
    API endpoint to switch the student's active level in a session.
    1. Enforces lock rules (cannot jump to a level greater than max_unlocked_level).
    2. Updates the current_level parameter in the database session.
    3. Serves the active question at the front of the target level queue (marking it 'Seen' immediately).
    4. If the target level was already completed, enters Revision Mode and returns previous responses.
    """
    session_id = req.session_id
    target_level = req.target_level
    
    cursor = db.cursor()
    
    # Fetch active session
    cursor.execute("SELECT * FROM practice_sessions WHERE session_id = %s AND status = 'IN_PROGRESS';", (session_id,))
    session = cursor.fetchone()
    if not session:
        raise HTTPException(status_code=404, detail="Active session not found")
        
    student_id = session['student_id']
    max_unlocked = session['max_unlocked_level']
    
    # Parse target_level: it can be an integer rank (e.g. 3) or a string name (e.g. 'moderate', '3. Moderate')
    target_str = str(target_level).strip().lower()
    if '.' in target_str:
        parts = target_str.split('.', 1)
        try:
            target_lvl_int = int(parts[0].strip())
        except ValueError:
            lvl_name = parts[1].strip()
            target_lvl_int = get_level_rank(lvl_name)
    else:
        try:
            target_lvl_int = int(target_str)
        except ValueError:
            target_lvl_int = get_level_rank(target_str)
            
    if target_lvl_int == 99:
        raise HTTPException(status_code=400, detail=f"Invalid level name or rank: {target_level}")
        
    # Enforce lock: target level must be unlocked (less than or equal to max_unlocked)
    if target_lvl_int > max_unlocked:
        raise HTTPException(status_code=403, detail="Level is locked. Achieve 100% accuracy on the current level to unlock.")
        
    progress = session['session_progress']
    if str(target_lvl_int) not in progress.get('levels', {}):
        raise HTTPException(status_code=400, detail=f"Level {target_lvl_int} is not available for the selected topics in this session.")
        
    level_progress = progress['levels'][str(target_lvl_int)]
    
    # Update current level selection in session database entry
    cursor.execute("""
        UPDATE practice_sessions 
        SET current_level = %s, updated_at = CURRENT_TIMESTAMP
        WHERE session_id = %s;
    """, (target_lvl_int, session_id))
    
    active_question = None
    revision_mode = False
    history = {}
    
    # Serve questions or load historical answers depending on completion status
    if level_progress['queue']:
        # Active level has questions remaining in queue. Serve front question.
        active_qid = level_progress['queue'][0]
        query = (
            "SELECT question_id, question, options, topic, level, model "
            "FROM questions "
            "WHERE question_id = %s;"
        )
        cursor.execute(query, (active_qid,))
        active_question = cursor.fetchone()
        
        # Immediately mark the new active question as 'Seen' in logs
        if active_question:
            mark_question_as_seen(student_id, active_qid, active_question['level'], active_question['topic'], cursor)
    else:
        # Target level has already been completed. Serve in revision mode.
        revision_mode = True
        history = level_progress['answered']
        
    return {
        "current_level": target_lvl_int,
        "active_question": active_question,
        "revision_mode": revision_mode,
        "history": history
    }

@app.post('/api/session/exit')
def exit_session(req: SessionActionRequest, db=Depends(get_db)):
    """
    API endpoint to exit the practice session abruptly.
    Permanently deletes the active session row from the practice_sessions table (losing all temporary session progress).
    Database logs (including 'Seen' tags) and student streaks are preserved.
    """
    session_id = req.session_id
    cursor = db.cursor()
    
    # Delete the active session immediately to clear ongoing progress
    cursor.execute("DELETE FROM practice_sessions WHERE session_id = %s AND status = 'IN_PROGRESS' RETURNING session_id;", (session_id,))
    deleted = cursor.fetchone()
    
    if not deleted:
        raise HTTPException(status_code=404, detail="Active session not found")
        
    return {"message": "Session exited abruptly. Progress lost."}

@app.post('/api/session/submit')
def submit_session(req: SessionActionRequest, background_tasks: BackgroundTasks, db=Depends(get_db)):
    """
    API endpoint to submit the completed session.
    1. Marks practice session status as 'COMPLETED' (Asynchronously).
    2. Increments the student's test count (Asynchronously).
    3. Compiles a summary report detailing accuracy, timing, and topic-specific performance.
    4. Appends the report string into students.cumulative_report (Asynchronously).
    """
    session_id = req.session_id
    cursor = db.cursor()
    
    # Fetch active session
    cursor.execute("SELECT * FROM practice_sessions WHERE session_id = %s AND status = 'IN_PROGRESS';", (session_id,))
    session = cursor.fetchone()
    if not session:
        raise HTTPException(status_code=404, detail="Active session not found")
        
    student_id = session['student_id']
    progress = session['session_progress']
    
    # Collect all answered question IDs across all levels for batch topic querying
    answered_qids = []
    for lvl_rank, lvl_progress in progress['levels'].items():
        answered_qids.extend(list(lvl_progress['answered'].keys()))
        
    qid_to_topic = {}
    if answered_qids:
        cursor.execute("SELECT question_id, topic FROM questions WHERE question_id = ANY(%s);", (answered_qids,))
        qid_to_topic = {row['question_id']: row['topic'] for row in cursor.fetchall()}
    
    # Compile session report metrics
    total_questions = 0
    total_answered = 0
    correct_count = 0
    total_time = 0
    topic_summary = {}
    
    for lvl_rank, lvl_progress in progress['levels'].items():
        # Add up remaining queued questions plus answered ones to find total level count
        total_questions += len(lvl_progress['queue']) + len(lvl_progress['answered'])
        
        # Retrieve details of answered questions
        for qid, ans in lvl_progress['answered'].items():
            total_answered += 1
            total_time += ans.get('time_taken_seconds', 0)
            if ans.get('is_correct'):
                correct_count += 1
                
            # Retrieve topic from batch fetched dictionary
            q_topic = qid_to_topic.get(qid, 'Unknown')
            
            # Keep running track of topic-based performance metrics
            if q_topic not in topic_summary:
                topic_summary[q_topic] = {"answered": 0, "correct": 0}
            topic_summary[q_topic]["answered"] += 1
            if ans.get('is_correct'):
                topic_summary[q_topic]["correct"] += 1
                  
    # Calculate session accuracy percentage
    accuracy = (correct_count / total_answered * 100) if total_answered > 0 else 0.0
    
    # Create summary report payload dict
    report = {
        "session_id": session_id,
        "total_questions_in_pool": total_questions,
        "total_questions_answered": total_answered,
        "correct_answers": correct_count,
        "wrong_answers": total_answered - correct_count,
        "accuracy_percentage": round(accuracy, 2),
        "total_time_seconds": total_time,
        "average_time_per_question_seconds": round(total_time / total_answered, 2) if total_answered > 0 else 0,
        "topic_performance": topic_summary
    }
    
    # Complete session and save report asynchronously in background
    background_tasks.add_task(async_complete_session_worker, session_id, student_id, report)
    
    return report

# Start Uvicorn Web Server
if __name__ == '__main__':
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=True)
