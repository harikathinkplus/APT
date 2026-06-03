import os
import uuid
import json
import random
import asyncio
import asyncpg
from contextlib import asynccontextmanager
from passlib.hash import bcrypt as bcrypt_ctx
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Request, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from typing import List, Optional, Any

load_dotenv()

# ── Database connection parameters ──────────────────────────────────────────────
# Loaded from environment variables (see .env.example — never hard-code credentials)
DB_PARAMS = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME", "adaptive_practice_db"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD"),
}

# ── Lifespan: async pool startup / shutdown ──────────────────────────────────────
# asyncpg.Pool is created inside the running event loop (required by asyncpg).
# On shutdown the pool is cleanly drained before the process exits.
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(
        **DB_PARAMS,
        min_size=int(os.getenv("DB_POOL_MIN", 2)),
        max_size=int(os.getenv("DB_POOL_MAX", 5)),
    )
    yield
    await app.state.pool.close()

# ── FastAPI application ──────────────────────────────────────────────────────────
app = FastAPI(title="Adaptive Practice Tool API", lifespan=lifespan)

# ── CORS ────────────────────────────────────────────────────────────────────────
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Exception handlers ───────────────────────────────────────────────────────────

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Format HTTPExceptions as {"error": "message"}
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Format RequestValidationError as {"error": "message"}
    details = exc.errors()
    error_msg = details[0]["msg"] if details else "Validation error"
    field = " -> ".join(str(loc) for loc in details[0]["loc"]) if details else ""
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": f"Validation failed: {error_msg} at {field}"},
    )

# ── Async DB dependency ──────────────────────────────────────────────────────────
# Acquires a connection from the pool for the lifetime of the request, then
# releases it back automatically — no manual putconn() needed.
async def get_db(request: Request):
    async with request.app.state.pool.acquire() as conn:
        yield conn

# ── Password helpers (bcrypt offloaded to thread executor) ──────────────────────
# bcrypt is CPU-intensive. Running it directly in an async function would block
# the event loop and stall all other concurrent requests on this worker.
# run_in_executor() delegates the work to a thread, keeping the loop free.

async def hash_password(password: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, bcrypt_ctx.hash, password)

async def verify_password(plain: str, hashed: str) -> bool:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, bcrypt_ctx.verify, plain, hashed)

# ── Utility helpers ──────────────────────────────────────────────────────────────

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

async def mark_question_as_seen(student_id, question_id, level, topic, conn):
    # Marks a question as 'Seen' immediately when it is served to the student.
    existing = await conn.fetchrow("""
        SELECT log_id FROM logs
        WHERE student_id = $1 AND question_id = $2 AND selected_option IS NULL;
    """, student_id, question_id)

    if not existing:
        await conn.execute(
            "INSERT INTO logs (student_id, question_id, correct_wrong, level, topic) "
            "VALUES ($1, $2, 'Seen', $3, $4);",
            student_id, question_id, level, topic
        )

# ── Background task workers (async — run in FastAPI's background task runner) ───
# These run after the HTTP response is already sent, so they don't add latency.
# They each acquire their own connection from the pool.

async def async_log_answer_and_next_seen_worker(
    pool, student_id, question_id, selected_option,
    status_str, time_taken, level, topic, next_question
):
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                # 1. Update the existing 'Seen' log entry to register the submitted answer
                result = await conn.execute("""
                    UPDATE logs
                    SET selected_option = $1, correct_wrong = $2, time_taken_seconds = $3
                    WHERE student_id = $4 AND question_id = $5 AND selected_option IS NULL;
                """, selected_option, status_str, time_taken, student_id, question_id)

                # Fallback: if no 'Seen' log existed, insert a full entry directly
                if result == "UPDATE 0":
                    await conn.execute("""
                        INSERT INTO logs
                            (student_id, question_id, selected_option, correct_wrong, level, topic, time_taken_seconds)
                        VALUES ($1, $2, $3, $4, $5, $6, $7);
                    """, student_id, question_id, selected_option, status_str, level, topic, time_taken)

                # 2. Immediately mark the next served question as 'Seen'
                if next_question:
                    next_qid = next_question['question_id']
                    existing = await conn.fetchrow("""
                        SELECT log_id FROM logs
                        WHERE student_id = $1 AND question_id = $2 AND selected_option IS NULL;
                    """, student_id, next_qid)
                    if not existing:
                        await conn.execute("""
                            INSERT INTO logs (student_id, question_id, correct_wrong, level, topic)
                            VALUES ($1, $2, 'Seen', $3, $4);
                        """, student_id, next_qid, next_question['level'], next_question['topic'])
        except Exception as e:
            print(f"[BG] log/next-seen task failed: {e}")

async def async_complete_session_worker(pool, session_id, student_id, report):
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE practice_sessions SET status = 'COMPLETED', updated_at = CURRENT_TIMESTAMP "
                    "WHERE session_id = $1;",
                    session_id
                )
                await conn.execute(
                    "UPDATE students SET no_of_tests = no_of_tests + 1 WHERE student_id = $1;",
                    student_id
                )
                # Insert structured report into session_reports (replaces unbounded cumulative_report TEXT)
                await conn.execute("""
                    INSERT INTO session_reports (session_id, student_id, report)
                    VALUES ($1, $2, $3::jsonb);
                """, session_id, student_id, json.dumps(report))
        except Exception as e:
            print(f"[BG] complete-session task failed: {e}")

# ── Pydantic schemas ─────────────────────────────────────────────────────────────

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

# ── Auth endpoints ───────────────────────────────────────────────────────────────

@app.post('/api/auth/register', status_code=201)
async def register(req: RegisterRequest, conn=Depends(get_db)):
    """
    Register a new student.
    Hashes the password with bcrypt (offloaded to thread) and inserts into students.
    """
    existing = await conn.fetchrow("SELECT student_id FROM students WHERE email = $1;", req.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already exists")

    student_id = str(uuid.uuid4())
    password_hash = await hash_password(req.password)

    try:
        student = await conn.fetchrow("""
            INSERT INTO students (student_id, student_name, email, password_hash)
            VALUES ($1, $2, $3, $4)
            RETURNING student_id, student_name, email, streak;
        """, student_id, req.student_name, req.email, password_hash)
        return dict(student)
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=400, detail="Email or Student ID already exists")

@app.post('/api/auth/login')
async def login(req: LoginRequest, conn=Depends(get_db)):
    """
    Authenticate an existing student.
    bcrypt.verify is offloaded to a thread executor to avoid blocking the event loop.
    """
    student = await conn.fetchrow("SELECT * FROM students WHERE email = $1;", req.email)

    if student and await verify_password(req.password, student['password_hash']):
        student_data = dict(student)
        student_data.pop('password_hash')  # Never expose the hash in the response
        return student_data

    raise HTTPException(status_code=401, detail="Invalid email or password")

# ── Dashboard endpoints ──────────────────────────────────────────────────────────

@app.get('/api/topics')
async def get_topics(student_id: Optional[str] = None, conn=Depends(get_db)):
    """
    Fetch all available practice topics.
    If student_id is provided, includes each topic's highest streak for that student.
    """
    topics_rows = await conn.fetch("SELECT DISTINCT topic FROM questions WHERE topic IS NOT NULL;")
    topics = [row['topic'] for row in topics_rows]

    topic_streaks = {}
    if student_id:
        streak_rows = await conn.fetch(
            "SELECT topic, highest_streak FROM student_topic_streaks WHERE student_id = $1;",
            student_id
        )
        topic_streaks = {row['topic']: row['highest_streak'] for row in streak_rows}

    return [{"topic": t, "highest_streak": topic_streaks.get(t, 0)} for t in topics]

@app.get('/api/leaderboard')
async def get_leaderboard(conn=Depends(get_db)):
    """
    Fetch the leaderboard — top 10 students ranked by highest global streak.
    """
    rows = await conn.fetch("""
        SELECT student_id, student_name, streak
        FROM students
        ORDER BY streak DESC
        LIMIT 10;
    """)
    return [dict(r) for r in rows]

# ── Session endpoints ────────────────────────────────────────────────────────────

@app.post('/api/session/start')
async def start_session(req: StartSessionRequest, conn=Depends(get_db)):
    """
    Start a new practice session for a student.
    1. Cancels any active sessions (marks them ABANDONED).
    2. Builds difficulty queues (ranks 1–5) with up to 10 random questions each.
    3. Anti-repetition: excludes previously seen questions unless all are seen.
    4. Marks the first question as 'Seen' in logs immediately.
    """
    student_id = req.student_id
    selected_topics = req.topics

    student = await conn.fetchrow("SELECT student_id FROM students WHERE student_id = $1;", student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    # Cancel and abandon any active sessions for this student
    await conn.execute("""
        UPDATE practice_sessions
        SET status = 'ABANDONED'
        WHERE student_id = $1 AND status = 'IN_PROGRESS';
    """, student_id)

    # Fetch all previously seen question IDs for anti-repetition logic
    answered_rows = await conn.fetch(
        "SELECT DISTINCT question_id FROM logs WHERE student_id = $1;", student_id
    )
    answered_qids = set(row['question_id'] for row in answered_rows)

    # Query all questions for selected topics
    all_questions = await conn.fetch(
        "SELECT question_id, question, options, correct_answer, topic, level, model "
        "FROM questions WHERE topic = ANY($1::text[]);",
        selected_topics
    )

    if not all_questions:
        raise HTTPException(status_code=404, detail="No questions found for the selected topics")

    # Convert asyncpg Records to mutable dicts
    all_questions = [dict(q) for q in all_questions]

    # Identify topics that use model-based question grouping
    topics_with_models = {q['topic'] for q in all_questions if q.get('model')}

    # Group questions by difficulty rank (1–5)
    level_questions: dict = {}
    for q in all_questions:
        lvl_rank = get_level_rank(q['level'] or 'Easy')
        if lvl_rank == 99:
            continue
        level_questions.setdefault(lvl_rank, []).append(q)

    # Build FIFO queues for each difficulty rank
    levels_progress: dict = {}
    for rank in range(1, 6):
        q_pool = level_questions.get(rank, [])
        if not q_pool:
            continue

        # Group questions within this rank by topic
        by_topic: dict = {}
        for q in q_pool:
            by_topic.setdefault(q['topic'], []).append(q)

        # Build per-topic candidate lists with anti-repetition and model coverage
        candidates_by_topic: dict = {}
        for t, q_list in by_topic.items():
            unseen = [q for q in q_list if q['question_id'] not in answered_qids]
            seen   = [q for q in q_list if q['question_id'] in answered_qids]
            random.shuffle(unseen)
            random.shuffle(seen)
            # Prefer unseen; fall back to seen only if all questions are exhausted
            allowed_for_topic = unseen if unseen else seen

            if t in topics_with_models:
                models_in_level = {q['model'] for q in allowed_for_topic if q.get('model')}
                if models_in_level:
                    by_model = {m: [] for m in models_in_level}
                    no_model_qs = []
                    for q in allowed_for_topic:
                        (by_model[q['model']] if q.get('model') else no_model_qs).append(q)

                    for m in by_model:
                        random.shuffle(by_model[m])
                    random.shuffle(no_model_qs)

                    # Round-robin across models to ensure full model coverage
                    active_models = list(models_in_level)
                    random.shuffle(active_models)
                    model_indices = {m: 0 for m in active_models}
                    topic_candidates: list = []
                    added = True
                    while added:
                        added = False
                        for m in active_models:
                            idx = model_indices[m]
                            if idx < len(by_model[m]):
                                topic_candidates.append(by_model[m][idx])
                                model_indices[m] += 1
                                added = True
                    topic_candidates.extend(no_model_qs)
                    candidates_by_topic[t] = topic_candidates
                else:
                    candidates_by_topic[t] = allowed_for_topic
            else:
                candidates_by_topic[t] = allowed_for_topic

        # Round-robin across topics to interleave questions from different subjects
        selected_qs: list = []
        active_topics = list(candidates_by_topic.keys())
        random.shuffle(active_topics)
        topic_indices = {t: 0 for t in active_topics}
        added = True
        while len(selected_qs) < 10 and added:
            added = False
            for t in active_topics:
                idx = topic_indices[t]
                if idx < len(candidates_by_topic[t]):
                    selected_qs.append(candidates_by_topic[t][idx])
                    topic_indices[t] += 1
                    added = True
                    if len(selected_qs) == 10:
                        break

        selected = [q['question_id'] for q in selected_qs]
        if not selected:
            continue

        # O(1) pointer-based queue — avoids O(n) list.pop(0) on every advance
        levels_progress[str(rank)] = {
            "queue":         selected,
            "current_index": 0,
            "answered":      {}
        }

    sorted_ranks = sorted(int(r) for r in levels_progress)
    if not sorted_ranks:
        raise HTTPException(status_code=404, detail="No questions available for the selected topics")

    session_progress = {"levels": levels_progress}
    session_id       = str(uuid.uuid4())
    initial_level    = sorted_ranks[0]
    initial_qid      = levels_progress[str(initial_level)]["queue"][0]

    session_row = await conn.fetchrow("""
        INSERT INTO practice_sessions
            (session_id, student_id, selected_topics, current_level, max_unlocked_level, session_progress, status)
        VALUES ($1, $2, $3::text[], $4, $5, $6::jsonb, 'IN_PROGRESS')
        RETURNING session_id, current_level, max_unlocked_level;
    """, session_id, student_id, selected_topics, initial_level, initial_level, json.dumps(session_progress))

    # Fetch and immediately mark the first question as 'Seen'
    active_question = await conn.fetchrow(
        "SELECT question_id, question, options, topic, level, model FROM questions WHERE question_id = $1;",
        initial_qid
    )
    if active_question:
        await mark_question_as_seen(
            student_id, initial_qid, active_question['level'], active_question['topic'], conn
        )
        active_question = dict(active_question)

    return {
        "session_id":         session_row['session_id'],
        "current_level":      session_row['current_level'],
        "max_unlocked_level": session_row['max_unlocked_level'],
        "active_question":    active_question,
        "queue_length":       len(levels_progress[str(initial_level)]["queue"])
    }

@app.post('/api/session/submit-answer')
async def submit_answer(
    req: SubmitAnswerRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    conn=Depends(get_db)
):
    """
    Submit an answer for the active question.
    1. Validates FIFO queue order — must answer the question at the current pointer.
    2. Compares selected option with correct_answer.
    3. DB log update + next-seen insertion dispatched asynchronously (no added latency).
    4. Updates global streak and topic streak (resets on wrong answer).
    5. Advances the queue pointer (O(1)).
    6. If level completes with 100% accuracy, unlocks the next difficulty rank.
    7. Returns the next question in the queue.
    """
    session_id      = req.session_id
    question_id     = req.question_id
    selected_option = req.selected_option

    session = await conn.fetchrow(
        "SELECT * FROM practice_sessions WHERE session_id = $1 AND status = 'IN_PROGRESS';",
        session_id
    )
    if not session:
        raise HTTPException(status_code=404, detail="Active session not found")

    student_id    = session['student_id']
    # asyncpg returns jsonb columns as Python dicts; guard against string just in case
    progress      = session['session_progress']
    if isinstance(progress, str):
        progress = json.loads(progress)
    current_level = str(session['current_level'])
    level_progress = progress['levels'][current_level]
    current_idx    = level_progress.get('current_index', 0)

    # Enforce FIFO order — reject out-of-sequence submissions
    if current_idx >= len(level_progress['queue']) or level_progress['queue'][current_idx] != question_id:
        raise HTTPException(status_code=400, detail="Submitted question is not at the front of the queue")

    question = await conn.fetchrow("SELECT * FROM questions WHERE question_id = $1;", question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    correct_answer = question['correct_answer']
    topic          = question['topic']
    level          = question['level']

    # Derive server-side time taken from the 'Seen' log timestamp (more reliable than client)
    log_row = await conn.fetchrow("""
        SELECT log_id, created_at FROM logs
        WHERE student_id = $1 AND question_id = $2 AND selected_option IS NULL
        ORDER BY created_at DESC LIMIT 1;
    """, student_id, question_id)

    if log_row:
        elapsed_row = await conn.fetchrow(
            "SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - $1))::INT AS elapsed;",
            log_row['created_at']
        )
        time_taken = max(1, elapsed_row['elapsed'] if elapsed_row['elapsed'] is not None else 1)
    else:
        time_taken = req.time_taken_seconds or 0

    # Check correctness (case-insensitive, trimmed)
    is_correct = (str(selected_option).strip().lower() == str(correct_answer).strip().lower())
    status_str = 'Correct' if is_correct else 'Wrong'

    # Update streak counters
    if is_correct:
        student_streak = await conn.fetchrow("""
            UPDATE students
            SET
                current_global_streak = current_global_streak + 1,
                streak = GREATEST(streak, current_global_streak + 1)
            WHERE student_id = $1
            RETURNING current_global_streak, streak;
        """, student_id)
        current_global = student_streak['current_global_streak']

        await conn.execute("""
            INSERT INTO student_topic_streaks (student_id, topic, current_streak, highest_streak)
            VALUES ($1, $2, 1, 1)
            ON CONFLICT (student_id, topic) DO UPDATE
            SET
                current_streak  = student_topic_streaks.current_streak + 1,
                highest_streak  = GREATEST(student_topic_streaks.highest_streak,
                                           student_topic_streaks.current_streak + 1);
        """, student_id, topic)
    else:
        await conn.execute(
            "UPDATE students SET current_global_streak = 0 WHERE student_id = $1;", student_id
        )
        current_global = 0

        await conn.execute("""
            INSERT INTO student_topic_streaks (student_id, topic, current_streak, highest_streak)
            VALUES ($1, $2, 0, 0)
            ON CONFLICT (student_id, topic) DO UPDATE
            SET current_streak = 0;
        """, student_id, topic)

    # Advance the queue pointer — O(1) vs O(n) list.pop(0)
    level_progress['current_index'] = current_idx + 1

    # Record answer details in session state (topic/level stored here avoids a DB round-trip in submit_session)
    level_progress['answered'][question_id] = {
        "selected_option":   selected_option,
        "is_correct":        is_correct,
        "time_taken_seconds": time_taken,
        "topic":             topic,
        "level":             level,
    }

    # Check if the level queue is fully exhausted
    level_completed  = (level_progress['current_index'] >= len(level_progress['queue']))
    max_unlocked     = session['max_unlocked_level']
    accuracy_achieved = False

    if level_completed:
        # 100% accuracy required to unlock the next difficulty rank
        accuracy_achieved = all(ans['is_correct'] is True for ans in level_progress['answered'].values())

        if accuracy_achieved:
            sorted_ranks = sorted(int(r) for r in progress['levels'])
            try:
                curr_idx = sorted_ranks.index(int(current_level))
                if curr_idx + 1 < len(sorted_ranks):
                    next_rank    = sorted_ranks[curr_idx + 1]
                    max_unlocked = max(max_unlocked, next_rank)
            except ValueError:
                pass

    # Persist updated session progress and unlock state
    await conn.execute("""
        UPDATE practice_sessions
        SET session_progress = $1::jsonb, max_unlocked_level = $2, updated_at = CURRENT_TIMESTAMP
        WHERE session_id = $3;
    """, json.dumps(progress), max_unlocked, session_id)

    # Fetch next question if the level is still active
    next_question = None
    if not level_completed:
        next_qid = level_progress['queue'][level_progress['current_index']]
        nq = await conn.fetchrow(
            "SELECT question_id, question, options, topic, level, model FROM questions WHERE question_id = $1;",
            next_qid
        )
        next_question = dict(nq) if nq else None

    # Dispatch DB log write + next-seen insert asynchronously (response is already sent before this runs)
    background_tasks.add_task(
        async_log_answer_and_next_seen_worker,
        pool=request.app.state.pool,
        student_id=student_id,
        question_id=question_id,
        selected_option=selected_option,
        status_str=status_str,
        time_taken=time_taken,
        level=level,
        topic=topic,
        next_question=next_question,
    )

    return {
        "is_correct":           is_correct,
        "correct_answer":       correct_answer,
        "current_global_streak": current_global,
        "max_unlocked_level":   max_unlocked,
        "level_completed":      level_completed,
        "accuracy_achieved":    accuracy_achieved,
        "next_question":        next_question,
    }

@app.post('/api/session/change-level')
async def change_level(req: ChangeLevelRequest, conn=Depends(get_db)):
    """
    Switch the student's active difficulty level within a session.
    1. Enforces the lock rule (cannot exceed max_unlocked_level).
    2. Persists current_level update in the DB.
    3. Serves the front-of-queue question for the target level (marking it 'Seen').
    4. If the target level is already complete, enters Revision Mode.
    """
    session_id   = req.session_id
    target_level = req.target_level

    session = await conn.fetchrow(
        "SELECT * FROM practice_sessions WHERE session_id = $1 AND status = 'IN_PROGRESS';",
        session_id
    )
    if not session:
        raise HTTPException(status_code=404, detail="Active session not found")

    student_id   = session['student_id']
    max_unlocked = session['max_unlocked_level']

    # Parse target_level: accepts int rank, string name, or "3. Moderate" formatted strings
    target_str = str(target_level).strip().lower()
    if '.' in target_str:
        parts = target_str.split('.', 1)
        try:
            target_lvl_int = int(parts[0].strip())
        except ValueError:
            target_lvl_int = get_level_rank(parts[1].strip())
    else:
        try:
            target_lvl_int = int(target_str)
        except ValueError:
            target_lvl_int = get_level_rank(target_str)

    if target_lvl_int == 99:
        raise HTTPException(status_code=400, detail=f"Invalid level name or rank: {target_level}")

    if target_lvl_int > max_unlocked:
        raise HTTPException(
            status_code=403,
            detail="Level is locked. Achieve 100% accuracy on the current level to unlock."
        )

    progress = session['session_progress']
    if isinstance(progress, str):
        progress = json.loads(progress)

    if str(target_lvl_int) not in progress.get('levels', {}):
        raise HTTPException(
            status_code=400,
            detail=f"Level {target_lvl_int} is not available for the selected topics in this session."
        )

    level_progress = progress['levels'][str(target_lvl_int)]

    await conn.execute("""
        UPDATE practice_sessions
        SET current_level = $1, updated_at = CURRENT_TIMESTAMP
        WHERE session_id = $2;
    """, target_lvl_int, session_id)

    active_question = None
    revision_mode   = False
    history         = {}

    current_idx = level_progress.get('current_index', 0)
    if current_idx < len(level_progress['queue']):
        # Level has remaining questions — serve the front of queue
        active_qid = level_progress['queue'][current_idx]
        aq = await conn.fetchrow(
            "SELECT question_id, question, options, topic, level, model FROM questions WHERE question_id = $1;",
            active_qid
        )
        if aq:
            active_question = dict(aq)
            await mark_question_as_seen(student_id, active_qid, aq['level'], aq['topic'], conn)
    else:
        # Level already completed — enter Revision Mode, return historical answers
        revision_mode = True
        history       = level_progress['answered']

    return {
        "current_level":   target_lvl_int,
        "active_question": active_question,
        "revision_mode":   revision_mode,
        "history":         history,
    }

@app.post('/api/session/exit')
async def exit_session(req: SessionActionRequest, conn=Depends(get_db)):
    """
    Exit the practice session abruptly.
    Deletes the session row (progress is lost). Logs and streaks are preserved.
    """
    deleted = await conn.fetchrow(
        "DELETE FROM practice_sessions WHERE session_id = $1 AND status = 'IN_PROGRESS' RETURNING session_id;",
        req.session_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Active session not found")

    return {"message": "Session exited abruptly. Progress lost."}

@app.post('/api/session/submit')
async def submit_session(
    req: SessionActionRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    conn=Depends(get_db)
):
    """
    Submit a completed session and generate a performance report.
    1. Compiles accuracy, timing, and topic-level performance metrics.
    2. Marks session as 'COMPLETED' and saves report to session_reports (asynchronously).
    3. Increments the student's total test count (asynchronously).
    Returns the report immediately — DB writes happen in the background.
    """
    session_id = req.session_id

    session = await conn.fetchrow(
        "SELECT * FROM practice_sessions WHERE session_id = $1 AND status = 'IN_PROGRESS';",
        session_id
    )
    if not session:
        raise HTTPException(status_code=404, detail="Active session not found")

    student_id = session['student_id']
    progress   = session['session_progress']
    if isinstance(progress, str):
        progress = json.loads(progress)

    # Compile session report metrics (topic is stored in the answered dict — no extra DB query)
    total_questions = 0
    total_answered  = 0
    correct_count   = 0
    total_time      = 0
    topic_summary: dict = {}

    for lvl_rank, lvl_progress in progress['levels'].items():
        # Queue is never mutated (pointer-based), so length == initial pool size
        total_questions += len(lvl_progress['queue'])

        for qid, ans in lvl_progress['answered'].items():
            total_answered += 1
            total_time     += ans.get('time_taken_seconds', 0)
            if ans.get('is_correct'):
                correct_count += 1

            q_topic = ans.get('topic', 'Unknown')
            if q_topic not in topic_summary:
                topic_summary[q_topic] = {"answered": 0, "correct": 0}
            topic_summary[q_topic]["answered"] += 1
            if ans.get('is_correct'):
                topic_summary[q_topic]["correct"] += 1

    accuracy = (correct_count / total_answered * 100) if total_answered > 0 else 0.0

    report = {
        "session_id":                      session_id,
        "total_questions_in_pool":          total_questions,
        "total_questions_answered":         total_answered,
        "correct_answers":                  correct_count,
        "wrong_answers":                    total_answered - correct_count,
        "accuracy_percentage":              round(accuracy, 2),
        "total_time_seconds":               total_time,
        "average_time_per_question_seconds": round(total_time / total_answered, 2) if total_answered > 0 else 0,
        "topic_performance":                topic_summary,
    }

    # Mark session complete and save report asynchronously — response returns immediately
    background_tasks.add_task(
        async_complete_session_worker, request.app.state.pool, session_id, student_id, report
    )

    return report

# ── Health check ─────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    """Health check endpoint for load balancers and uptime monitors."""
    return {"status": "ok"}

# ── Entry point ──────────────────────────────────────────────────────────────────
# For development: python app.py
# For production:  gunicorn -c gunicorn.conf.py app:app

if __name__ == '__main__':
    import uvicorn
    _workers = int(os.getenv("WORKERS", 1))
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        workers=_workers,
        # reload is incompatible with workers > 1; restrict to single-worker dev mode
        reload=(_workers == 1),
    )
