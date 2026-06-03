import requests
import sys
import psycopg2

API_URL = "http://127.0.0.1:5000/api"
DB_PARAMS = {
    "host": "localhost",
    "port": 5432,
    "database": "adaptive_practice_db",
    "user": "postgres",
    "password": "Satya@17"
}

def clear_student_data(email):
    conn = psycopg2.connect(**DB_PARAMS)
    cursor = conn.cursor()
    cursor.execute("SELECT student_id FROM students WHERE email = %s;", (email,))
    row = cursor.fetchone()
    if row:
        student_id = row[0]
        cursor.execute("DELETE FROM logs WHERE student_id = %s;", (student_id,))
        cursor.execute("DELETE FROM practice_sessions WHERE student_id = %s;", (student_id,))
        cursor.execute("DELETE FROM student_topic_streaks WHERE student_id = %s;", (student_id,))
        cursor.execute("DELETE FROM students WHERE student_id = %s;", (student_id,))
        conn.commit()
    conn.close()

def main():
    print("=== Adaptive Practice Tool Logic Verification ===")
    
    email = "test_logic_student@test.com"
    clear_student_data(email)
    
    # 1. Register student
    resp = requests.post(f"{API_URL}/auth/register", json={
        "student_name": "LogicTester",
        "email": email,
        "password": "Password123"
    })
    if resp.status_code != 201:
        print("Registration failed:", resp.text)
        sys.exit(1)
    
    student = resp.json()
    student_id = student['student_id']
    print(f"Registered student {student_id}")
    
    # Let's inspect Math Easy questions. We have M_L1_Q1 and M_L1_Q2. (2 questions)
    # Let's start session 1 for topic Math
    resp = requests.post(f"{API_URL}/session/start", json={
        "student_id": student_id,
        "topics": ["Math"]
    })
    if resp.status_code != 200:
        print("Failed to start session:", resp.text)
        sys.exit(1)
        
    s1 = resp.json()
    s1_id = s1['session_id']
    q1_id = s1['active_question']['question_id']
    print(f"Started Session 1. ID: {s1_id}. Active Question: {q1_id}")
    
    # Verify that the active question is immediately marked as 'Seen' in logs
    conn = psycopg2.connect(**DB_PARAMS)
    cursor = conn.cursor()
    cursor.execute("SELECT question_id, correct_wrong FROM logs WHERE student_id = %s;", (student_id,))
    logs = cursor.fetchall()
    print("Logs in DB after starting Session 1:")
    for log in logs:
        print(f" - Question: {log[0]}, Status: {log[1]}")
    
    if len(logs) != 1 or logs[0][0] != q1_id or logs[0][1] != 'Seen':
        print("ERROR: Question not immediately logged as 'Seen' correctly!")
        sys.exit(1)
        
    # Exit session 1
    resp = requests.post(f"{API_URL}/session/exit", json={"session_id": s1_id})
    if resp.status_code != 200:
        print("Failed to exit session:", resp.text)
        sys.exit(1)
    print("Exited Session 1 successfully.")
    
    # Start session 2 for topic Math
    resp = requests.post(f"{API_URL}/session/start", json={
        "student_id": student_id,
        "topics": ["Math"]
    })
    if resp.status_code != 200:
        print("Failed to start session 2:", resp.text)
        sys.exit(1)
        
    s2 = resp.json()
    s2_id = s2['session_id']
    q2_id = s2['active_question']['question_id']
    print(f"Started Session 2. ID: {s2_id}. Active Question: {q2_id}")
    
    # Verify that Q2 is different from Q1 (since Q1 was already marked as Seen)
    if q2_id == q1_id:
        print("ERROR: Question repeated even though student exited without answering, and other unseen questions exist!")
        sys.exit(1)
    print(f"Success: Served a different question ({q2_id}) in the new session!")
    
    # Verify logs in DB
    cursor.execute("SELECT question_id, correct_wrong FROM logs WHERE student_id = %s ORDER BY log_id ASC;", (student_id,))
    logs = cursor.fetchall()
    print("Logs in DB after starting Session 2:")
    for log in logs:
        print(f" - Question: {log[0]}, Status: {log[1]}")
        
    if len(logs) != 2:
        print("ERROR: Expected 2 logs in DB (one for each seen question).")
        sys.exit(1)
        
    # Exit session 2
    resp = requests.post(f"{API_URL}/session/exit", json={"session_id": s2_id})
    print("Exited Session 2 successfully.")
    
    # At this point, the student has seen both Math Easy questions (M_L1_Q1 and M_L1_Q2).
    # Since they have seen all questions of topic Math and level Easy:
    # Starting a new Math session should allow the questions to repeat.
    # Let's verify this!
    resp = requests.post(f"{API_URL}/session/start", json={
        "student_id": student_id,
        "topics": ["Math"]
    })
    if resp.status_code != 200:
        print("Failed to start session 3:", resp.text)
        sys.exit(1)
        
    s3 = resp.json()
    s3_id = s3['session_id']
    q3_id = s3['active_question']['question_id']
    print(f"Started Session 3. ID: {s3_id}. Active Question: {q3_id}")
    
    # Verify that a question repeated (it must be either Q1 or Q2 since there are only 2 Math Easy questions)
    if q3_id not in [q1_id, q2_id]:
        print("ERROR: Served a question that is not in the Math Easy pool!")
        sys.exit(1)
    print(f"Success: Questions successfully repeated ({q3_id}) because all Math Easy questions have been seen!")
    
    # Now let's test that if we select multiple topics (Math and Science):
    # Science Easy has 1 question ("S_L1_Q1") which is unseen.
    # Math Easy has 2 questions, both seen.
    # When starting a session for Math and Science:
    # - Science Easy must NOT repeat (it must serve the unseen "S_L1_Q1").
    # - Math Easy can repeat because all are seen.
    # Let's clean up and verify this specific mixed case.
    clear_student_data(email)
    
    # Register student again
    resp = requests.post(f"{API_URL}/auth/register", json={
        "student_name": "LogicTester",
        "email": email,
        "password": "Password123"
    })
    student_id = resp.json()['student_id']
    
    # Make student see all Math Easy questions first by starting and exiting sessions
    # Start & exit for Math (sees Q1)
    s_m1 = requests.post(f"{API_URL}/session/start", json={"student_id": student_id, "topics": ["Math"]}).json()
    requests.post(f"{API_URL}/session/exit", json={"session_id": s_m1['session_id']})
    
    # Start & exit for Math (sees Q2)
    s_m2 = requests.post(f"{API_URL}/session/start", json={"student_id": student_id, "topics": ["Math"]}).json()
    requests.post(f"{API_URL}/session/exit", json={"session_id": s_m2['session_id']})
    
    # Now they have seen all Math Easy questions (2/2). They have seen 0/1 Science Easy questions.
    # Let's start a session with BOTH topics: Math and Science.
    resp = requests.post(f"{API_URL}/session/start", json={
        "student_id": student_id,
        "topics": ["Math", "Science"]
    })
    if resp.status_code != 200:
        print("Failed to start mixed session:", resp.text)
        sys.exit(1)
        
    mixed_session = resp.json()
    m_session_id = mixed_session['session_id']
    print(f"Started Mixed Session (Math + Science). ID: {m_session_id}")
    
    # Let's query the practice_sessions table to inspect the generated queue for Level 2 (Easy)
    cursor.execute("SELECT session_progress FROM practice_sessions WHERE session_id = %s;", (m_session_id,))
    progress = cursor.fetchone()[0]
    easy_queue = progress['levels']['2']['queue']
    print(f"Generated Easy (rank 2) queue for mixed session: {easy_queue}")
    
    # Verify that:
    # 1. "S_L1_Q1" is in the queue (since it's the unseen Science question).
    # 2. At least one Math question is in the queue (repeating because all Math Easy questions are seen).
    if "S_L1_Q1" not in easy_queue:
        print("ERROR: Unseen Science question S_L1_Q1 not in the queue!")
        sys.exit(1)
        
    math_qids = [qid for qid in easy_queue if qid.startswith("M_L1")]
    if not math_qids:
        print("ERROR: No Math questions in the queue, even though we selected Math topic!")
        sys.exit(1)
        
    print("Success: Mixed topic queue generated correctly! Science was unseen (included), Math was fully seen (repeated).")
    
    # Exit mixed session
    requests.post(f"{API_URL}/session/exit", json={"session_id": m_session_id})
    clear_student_data(email)
    
    print("\nALL TESTS PASSED SUCCESSFULLY!")
    conn.close()

if __name__ == '__main__':
    main()
