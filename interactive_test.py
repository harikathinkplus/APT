import sys
import requests

API_URL = "http://127.0.0.1:5000/api"

def print_divider():
    print("-" * 50)

def interactive_session():
    print_divider()
    print("Welcome to the Adaptive Practice Tool Interactive Test Runner!")
    print("This client communicates with your Flask server (app.py) to test the logic.")
    print_divider()
    
    # 1. Login or Register
    print("Choose an option:")
    print("1. Register a new student")
    print("2. Login with existing student")
    choice = input("Enter choice (1 or 2, default is 2): ").strip() or "2"
    
    student_id = None
    if choice == "1":
        name = input("Enter student name: ").strip()
        email = input("Enter email: ").strip()
        password = input("Enter password: ").strip()
        
        try:
            resp = requests.post(f"{API_URL}/auth/register", json={
                "student_name": name,
                "email": email,
                "password": password
            })
            if resp.status_code == 201:
                student = resp.json()
                student_id = student['student_id']
                print(f"\nRegistration Successful! Student ID: {student_id}")
            else:
                print(f"\nError: {resp.json().get('error')}")
                sys.exit(1)
        except requests.exceptions.ConnectionError:
            print("\nError: Could not connect to Flask server. Make sure 'python app.py' is running in another terminal.")
            sys.exit(1)
    else:
        # Default fallback values for quick testing
        email = input("Enter email (default: satya@gmail.com): ").strip() or "satya@gmail.com"
        password = input("Enter password (default: Password123): ").strip() or "Password123"
        
        try:
            resp = requests.post(f"{API_URL}/auth/login", json={
                "email": email,
                "password": password
            })
            if resp.status_code == 200:
                student = resp.json()
                student_id = student['student_id']
                print(f"\nLogin Successful! Welome back, {student['student_name']}.")
            else:
                print(f"\nError: {resp.json().get('error')}")
                sys.exit(1)
        except requests.exceptions.ConnectionError:
            print("\nError: Could not connect to Flask server. Make sure 'python app.py' is running in another terminal.")
            sys.exit(1)

    # 2. Fetch Topics & Choose
    print_divider()
    print("Fetching available topics...")
    resp = requests.get(f"{API_URL}/topics?student_id={student_id}")
    topics = resp.json()
    
    if not topics:
        print("No topics found in the database. Make sure you seeded questions first!")
        sys.exit(1)
        
    print("\nAvailable Topics and your highest streaks:")
    for idx, t in enumerate(topics, 1):
        print(f"{idx}. {t['topic']} (Highest Streak: {t['highest_streak']})")
        
    selected_indices = input("\nChoose topic numbers (comma-separated, e.g. 1): ").strip().split(",")
    selected_topics = []
    for idx_str in selected_indices:
        try:
            idx = int(idx_str.strip()) - 1
            if 0 <= idx < len(topics):
                selected_topics.append(topics[idx]['topic'])
        except ValueError:
            pass
            
    if not selected_topics:
        print("No valid topic selected. Exiting.")
        sys.exit(1)

    # 3. Start Session
    print_divider()
    print(f"Starting practice session for topics: {selected_topics}...")
    resp = requests.post(f"{API_URL}/session/start", json={
        "student_id": student_id,
        "topics": selected_topics
    })
    
    if resp.status_code != 200:
        print("Failed to start session:", resp.json().get('error'))
        sys.exit(1)
        
    session = resp.json()
    session_id = session['session_id']
    current_level = session['current_level']
    max_unlocked = session['max_unlocked_level']
    active_question = session['active_question']
    
    level_names = {1: "Very Easy", 2: "Easy", 3: "Moderate", 4: "Hard", 5: "Very Hard"}
    
    # 4. Interactive loop
    while True:
        print_divider()
        lvl_name = level_names.get(int(current_level), f"Level {current_level}")
        print(f"CURRENT LEVEL: {lvl_name} (Max Unlocked Level: {level_names.get(max_unlocked, max_unlocked)})")
        
        if active_question is None:
            print("\nQueue is empty for this level! All questions answered.")
            print("What would you like to do?")
            print("- Type 'change' to select a different level")
            print("- Type 'submit' to complete the session and view the report")
            print("- Type 'exit' to pause and wipe progress")
        else:
            print(f"\nQuestion ID: {active_question['question_id']}")
            print(f"Question: {active_question['question']}")
            print("Options:")
            for opt in active_question['options']:
                print(f" - {opt}")
            
            print("\nOptions: [Enter answer] | [Type 'exit' to quit] | [Type 'change' to switch level] | [Type 'submit' to finish]")
            
        action = input("Your Input: ").strip()
        
        if action.lower() == "exit":
            print("\nExiting session. Wiping active progress...")
            resp = requests.post(f"{API_URL}/session/exit", json={"session_id": session_id})
            print(resp.json().get('message'))
            break
            
        elif action.lower() == "submit":
            print("\nSubmitting session and compiling report...")
            resp = requests.post(f"{API_URL}/session/submit", json={"session_id": session_id})
            if resp.status_code == 200:
                report = resp.json()
                print_divider()
                print("SESSION SUMMARY REPORT")
                print_divider()
                print(f"Total Questions in Pool: {report['total_questions_in_pool']}")
                print(f"Questions Answered: {report['total_questions_answered']}")
                print(f"Correct Answers: {report['correct_answers']}")
                print(f"Wrong Answers: {report['wrong_answers']}")
                print(f"Accuracy: {report['accuracy_percentage']}%")
                print(f"Average Time: {report['average_time_per_question_seconds']}s")
                print("\nTopic Breakdown:")
                for topic, details in report['topic_performance'].items():
                    print(f" - {topic}: {details['correct']}/{details['answered']} correct")
                print_divider()
            else:
                print("Failed to submit session:", resp.json().get('error'))
            break
            
        elif action.lower() == "change":
            print("\nAvailable Levels:")
            for rank, name in level_names.items():
                status = "Unlocked" if rank <= max_unlocked else "Locked"
                print(f" {rank}. {name} ({status})")
            target = input("Select level rank: ").strip()
            
            resp = requests.post(f"{API_URL}/session/change-level", json={
                "session_id": session_id,
                "target_level": target
            })
            
            if resp.status_code == 200:
                lvl_res = resp.json()
                current_level = lvl_res['current_level']
                active_question = lvl_res['active_question']
                print(f"\nLevel successfully changed to {level_names[int(current_level)]}!")
                if lvl_res.get('revision_mode'):
                    print("--- REVISION MODE ---")
                    print("You have already completed this level. Here are your previous answers:")
                    for qid, ans in lvl_res['history'].items():
                        status = "Correct" if ans['is_correct'] else "Wrong"
                        print(f" - Question {qid}: Selected '{ans['selected_option']}' ({status})")
            else:
                print(f"\nError: {resp.json().get('error')}")
                
        else:
            if active_question is None:
                print("Invalid option. Queue is empty. Type 'change', 'submit', or 'exit'.")
                continue
                
            time_taken = input("Time taken (seconds, default is 10): ").strip() or "10"
            try:
                time_taken = int(time_taken)
            except ValueError:
                time_taken = 10
                
            resp = requests.post(f"{API_URL}/session/submit-answer", json={
                "session_id": session_id,
                "question_id": active_question['question_id'],
                "selected_option": action,
                "time_taken_seconds": time_taken
            })
            
            if resp.status_code == 200:
                result = resp.json()
                print("\n--- RESULT ---")
                if result['is_correct']:
                    print("Correct Answer! (+1 streak)")
                else:
                    print(f"Wrong Answer! (Correct answer is: '{result['correct_answer']}') (Streak reset to 0)")
                    
                print(f"Global Streak: {result['current_global_streak']}")
                
                max_unlocked = result['max_unlocked_level']
                
                if result['level_completed']:
                    print("\nYou finished all questions in this level!")
                    if result['accuracy_achieved']:
                        print("100% ACCURACY ACHIEVED! Next level unlocked.")
                    else:
                        print("Did not achieve 100% accuracy. Next level remains locked. You must retake this level to progress.")
                        
                active_question = result['next_question']
            else:
                print(f"\nError: {resp.json().get('error')}")

if __name__ == "__main__":
    try:
        interactive_session()
    except KeyboardInterrupt:
        print("\nExiting test runner.")
