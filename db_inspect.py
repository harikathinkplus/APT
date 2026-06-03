import psycopg2
from psycopg2.extras import RealDictCursor

DB_PARAMS = {
    "host": "localhost",
    "port": 5432,
    "database": "adaptive_practice_db",
    "user": "postgres",
    "password": "Satya@17"
}

def inspect():
    conn = psycopg2.connect(**DB_PARAMS, cursor_factory=RealDictCursor)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM questions;")
    q_count = cursor.fetchone()['count']
    print(f"Total questions in database: {q_count}")
    
    cursor.execute("SELECT level, topic, model, COUNT(*) FROM questions GROUP BY level, topic, model;")
    print("\nQuestions grouped by level, topic, and model:")
    for row in cursor.fetchall():
        print(f" - Level: {row['level']}, Topic: {row['topic']}, Model: {row['model']}, Count: {row['count']}")
        
    cursor.execute("SELECT * FROM students;")
    print("\nStudents:")
    for row in cursor.fetchall():
        print(f" - ID: {row['student_id']}, Name: {row['student_name']}, Email: {row['email']}")
        
    cursor.execute("SELECT student_id, question_id, correct_wrong, level, topic, selected_option FROM logs;")
    print("\nLogs:")
    for row in cursor.fetchall():
        print(f" - Student: {row['student_id']}, QID: {row['question_id']}, Correct/Wrong/Seen: {row['correct_wrong']}, Option: {row['selected_option']}")
        
    conn.close()

if __name__ == '__main__':
    inspect()
