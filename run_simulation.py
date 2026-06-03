import requests
import sys
import psycopg2
import time

API_URL = "http://127.0.0.1:5000/api"
DB_PARAMS = {
    "host": "localhost",
    "port": 5432,
    "database": "adaptive_practice_db",
    "user": "postgres",
    "password": "Satya@17"
}

# Complete mapping of question IDs to correct answers
ANSWERS = {
    # Very Easy (Level 1)
    "M_L0_Q1": "8",
    "M_L0_Q2": "6",
    "S_L0_Q1": "Mars",
    "S_L0_Q2": "Green",
    
    # Easy (Level 2)
    "M_L1_Q1": "30",
    "M_L1_Q2": "35",
    "S_L1_Q1": "Oxygen",
    
    # Medium (Level 3)
    "M_L2_Q1": "132",
    "M_L2_Q2": "12",
    "S_L2_Q1": "H2O",
    
    # Hard (Level 4)
    "M_L3_Q1": "120",
    "M_L3_Q2": "625",
    "S_L3_Q1": "300,000 km/s",
    
    # Very Hard (Level 5)
    "M_L4_Q1": "4",
    "M_L4_Q2": "27",
    "S_L4_Q1": "Neutron",
    "S_L4_Q2": "Methane"
}

def clear_student_data(email):
    """Clean up student data from database to ensure a pristine test state."""
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

def print_step(title):
    print("\n" + "="*80)
    print(f"STEP: {title}")
    print("="*80)

def main():
    email = "simulation_student@test.com"
    print("Initializing Adaptive Practice Tool End-to-End Simulation...")
    clear_student_data(email)
    
    # 1. Register a student
    print_step("Registering a new student")
    resp = requests.post(f"{API_URL}/auth/register", json={
        "student_name": "SimulatedUser",
        "email": email,
        "password": "SecurePassword123"
    })
    if resp.status_code != 201:
        print(f"[-] Registration failed: {resp.text}")
        sys.exit(1)
    
    student = resp.json()
    student_id = student['student_id']
    print(f"[+] Successfully registered student: {student['student_name']} (ID: {student_id})")
    
    # 2. Start a session for topics "Math" and "Science"
    print_step("Starting a practice session with multiple topics: Math and Science")
    resp = requests.post(f"{API_URL}/session/start", json={
        "student_id": student_id,
        "topics": ["Math", "Science"]
    })
    if resp.status_code != 200:
        print(f"[-] Failed to start session: {resp.text}")
        sys.exit(1)
        
    session = resp.json()
    session_id = session['session_id']
    current_level = session['current_level']
    max_unlocked = session['max_unlocked_level']
    active_question = session['active_question']
    
    print(f"[+] Session started: ID = {session_id}")
    print(f"[+] Initial Active Level: {current_level} (Expected: 1 - Very Easy)")
    
    # ASSERTION: The session MUST start with Level 1 (Very Easy)
    if int(current_level) != 1:
        print(f"[-] ERROR: Session started with Level {current_level} instead of 1 (Very Easy)!")
        sys.exit(1)
    else:
        print("[+] Success: Session started with Very Easy level!")
        
    print(f"[+] Active Question: {active_question['question_id']} - \"{active_question['question']}\"")
    
    # 3. Simulate Level 1 (Very Easy) progress with 100% accuracy and time delay
    print_step("Simulating Level 1 (Very Easy) - Submitting correct answers after a 2-second delay")
    
    while active_question is not None:
        q_id = active_question['question_id']
        correct_answer = ANSWERS.get(q_id)
        
        print(f"\n---> Serving Question: {q_id} (Model: {active_question['model']})")
        print(f"     Question: \"{active_question['question']}\"")
        print("     Waiting 2 seconds to test server-side automatic time monitoring...")
        time.sleep(2)
        print(f"     Submitting Answer: {correct_answer} (no time_taken specified in body)")
        
        # We do NOT pass time_taken_seconds in the request payload to let the server monitor/calculate it itself!
        resp = requests.post(f"{API_URL}/session/submit-answer", json={
            "session_id": session_id,
            "question_id": q_id,
            "selected_option": correct_answer
        })
        if resp.status_code != 200:
            print(f"[-] Failed to submit answer: {resp.text}")
            sys.exit(1)
            
        res = resp.json()
        print(f"     Result: {'CORRECT' if res['is_correct'] else 'WRONG'}")
        print(f"     Current Global Streak: {res['current_global_streak']}")
        print(f"     Max Unlocked Level: {res['max_unlocked_level']}")
        
        # Verify the time logged in database for this answer is around 2 seconds (with a small retry loop for async writing)
        time_logged = None
        for _ in range(10):
            time.sleep(0.1)
            conn = psycopg2.connect(**DB_PARAMS)
            cursor = conn.cursor()
            cursor.execute("SELECT time_taken_seconds FROM logs WHERE student_id = %s AND question_id = %s;", (student_id, q_id))
            row = cursor.fetchone()
            conn.close()
            if row and row[0] is not None:
                time_logged = row[0]
                break
        print(f"     [Server Monitored Time]: {time_logged} seconds logged in database")
        if time_logged < 2:
            print("[-] Warning: Automatic time calculation was not applied or logged less than 2s.")
        else:
            print("[+] Success: Server successfully calculated and logged time elapsed without client specification!")
            
        if res['level_completed']:
            print(f"     [!] Level completed!")
            print(f"     [!] Accuracy achieved: {res['accuracy_achieved']}")
            if res['accuracy_achieved']:
                print(f"     [+] Success: Level 2 (Easy) is now UNLOCKED!")
            else:
                print(f"     [-] Level 2 (Easy) remains LOCKED.")
            max_unlocked = res['max_unlocked_level']
            break
            
        active_question = res['next_question']

    # 4. Switch to Level 2 (Easy)
    print_step("Changing level to Level 2 (Easy)")
    resp = requests.post(f"{API_URL}/session/change-level", json={
        "session_id": session_id,
        "target_level": 2
    })
    if resp.status_code != 200:
        print(f"[-] Failed to change level: {resp.text}")
        sys.exit(1)
        
    lvl_res = resp.json()
    current_level = lvl_res['current_level']
    active_question = lvl_res['active_question']
    print(f"[+] Active Level successfully changed to: {current_level} (Easy)")
    
    # 5. Complete Level 2 (Easy) with 100% accuracy to unlock Level 3
    print_step("Simulating Level 2 (Easy) - Answering all questions CORRECTLY to unlock Level 3")
    while active_question is not None:
        q_id = active_question['question_id']
        correct_answer = ANSWERS.get(q_id)
        
        print(f"\n---> Serving Question: {q_id} (Model: {active_question['model']})")
        print(f"     Submitting Answer: {correct_answer}")
        
        resp = requests.post(f"{API_URL}/session/submit-answer", json={
            "session_id": session_id,
            "question_id": q_id,
            "selected_option": correct_answer
        })
        if resp.status_code != 200:
            print(f"[-] Failed to submit answer: {resp.text}")
            sys.exit(1)
            
        res = resp.json()
        if res['level_completed']:
            print(f"     [!] Level completed!")
            if res['accuracy_achieved']:
                print(f"     [+] Success: Level 3 (Medium) is now UNLOCKED!")
            max_unlocked = res['max_unlocked_level']
            break
            
        active_question = res['next_question']

    # 6. Test robust parsing in change-level using the string name "moderate"
    print_step("Testing level change parsing - changing to unlocked Level 3 using string name 'moderate'")
    resp = requests.post(f"{API_URL}/session/change-level", json={
        "session_id": session_id,
        "target_level": "moderate"
    })
    print(f"[*] Response Code: {resp.status_code}")
    if resp.status_code == 200:
        lvl_res = resp.json()
        current_level = lvl_res['current_level']
        active_question = lvl_res['active_question']
        print(f"[+] Success: Active Level successfully changed to {current_level} (Medium) using level name string!")
    else:
        print(f"[-] Error: Failed to change level using string: {resp.text}")
        sys.exit(1)

    # 7. Complete Level 3 (Medium) with 1 wrong answer (to test locking of Hard level)
    print_step("Simulating Level 3 (Medium) - Submitting a WRONG answer for the first question")
    first = True
    while active_question is not None:
        q_id = active_question['question_id']
        if first:
            submitted_answer = "Wrong Option"
            first = False
        else:
            submitted_answer = ANSWERS.get(q_id)
            
        print(f"\n---> Serving Question: {q_id} (Model: {active_question['model']})")
        print(f"     Submitting Answer: {submitted_answer}")
        
        resp = requests.post(f"{API_URL}/session/submit-answer", json={
            "session_id": session_id,
            "question_id": q_id,
            "selected_option": submitted_answer
        })
        res = resp.json()
        if res['level_completed']:
            print(f"     [!] Level completed!")
            print(f"     [!] Accuracy achieved: {res['accuracy_achieved']}")
            if not res['accuracy_achieved']:
                print(f"     [+] Success: Level 4 (Hard) remains LOCKED because accuracy was not 100%.")
            max_unlocked = res['max_unlocked_level']
            break
            
        active_question = res['next_question']

    # 8. Try to change to Level 4 (Hard) using string "Hard" - should be LOCKED
    print_step("Lock Enforcement Check - Trying to access Level 4 (Hard) using level name 'Hard'")
    resp = requests.post(f"{API_URL}/session/change-level", json={
        "session_id": session_id,
        "target_level": "Hard"
    })
    print(f"[*] Response Code: {resp.status_code}")
    print(f"[*] Response Payload: {resp.json()}")
    if resp.status_code == 403:
        print("[+] Success: Access denied as expected! Lock rules are working perfectly.")
    else:
        print("[-] Error: Level 4 was accessed even though it should be locked!")
        sys.exit(1)

    # 9. Switch back to Level 1 (Very Easy) using enumeration string "1. Very Easy" to test Revision Mode & robust parsing
    print_step("Changing level back to Level 1 using enumeration '1. Very Easy' to test Revision Mode and parsing")
    resp = requests.post(f"{API_URL}/session/change-level", json={
        "session_id": session_id,
        "target_level": "1. Very Easy"
    })
    if resp.status_code != 200:
        print(f"[-] Failed to change level: {resp.text}")
        sys.exit(1)
        
    lvl_res = resp.json()
    print(f"[+] Response Code: {resp.status_code}")
    print(f"[*] Current Level: {lvl_res['current_level']}")
    print(f"[*] Revision Mode: {lvl_res.get('revision_mode')}")
    print(f"[*] History of Level 1 answers:")
    for qid, ans in lvl_res.get('history', {}).items():
         print(f"     - Question {qid}: Selected \"{ans['selected_option']}\", Correct: {ans['is_correct']}, Time taken: {ans.get('time_taken_seconds')}s")
         
    if lvl_res.get('revision_mode') is True:
        print("[+] Success: Revision Mode identified correctly and parsing was successful!")
    else:
        print("[-] Error: Revision Mode was not triggered for a completed level!")
        sys.exit(1)
        
    # 10. Submit the session and view report
    print_step("Submitting the session and retrieving the compiled Cumulative Report")
    resp = requests.post(f"{API_URL}/session/submit", json={
        "session_id": session_id
    })
    if resp.status_code != 200:
        print(f"[-] Failed to submit session: {resp.text}")
        sys.exit(1)
        
    report = resp.json()
    print(f"[+] Session Submitted Successfully!")
    print("\n" + "-"*50)
    print("SESSION SUMMARY REPORT")
    print("-"*50)
    print(f"Total Questions in Pool:            {report['total_questions_in_pool']}")
    print(f"Total Questions Answered:           {report['total_questions_answered']}")
    print(f"Correct Answers:                    {report['correct_answers']}")
    print(f"Wrong Answers:                      {report['wrong_answers']}")
    print(f"Accuracy Percentage:                {report['accuracy_percentage']}%")
    print(f"Total Time:                         {report['total_time_seconds']} seconds")
    print(f"Average Time per Question:          {report['average_time_per_question_seconds']} seconds")
    print("-"*50)
    
    # Clean up student data
    clear_student_data(email)
    print("\n[+] Cleaned up simulation student data successfully.")
    print("="*80)
    print("END-TO-END SIMULATION COMPLETED SUCCESSFULLY!")
    print("="*80)

if __name__ == '__main__':
    main()
