import psycopg2
from psycopg2.extras import Json

DB_PARAMS = {
    "host": "localhost",
    "port": 5432,
    "database": "adaptive_practice_db",
    "user": "postgres",
    "password": "Satya@17"
}

def seed():
    print("Connecting to database to seed questions...")
    conn = psycopg2.connect(**DB_PARAMS)
    cursor = conn.cursor()
    
    # Truncate tables for a clean test environment
    cursor.execute("TRUNCATE TABLE logs, practice_sessions, questions CASCADE;")
    
    questions = [
        # Math - Level 1 (Very Easy)
        ("M_L0_Q1", "What is 5 + 3?", ["7", "8", "9", "10"], "8", "Math", "Very Easy", "GPT-4"),
        ("M_L0_Q2", "What is 2 * 3?", ["5", "6", "7", "8"], "6", "Math", "Very Easy", "GPT-4"),

        # Math - Level 2 (Easy)
        ("M_L1_Q1", "What is 10 + 20?", ["15", "20", "30", "40"], "30", "Math", "Easy", "GPT-4"),
        ("M_L1_Q2", "What is 50 - 15?", ["25", "30", "35", "40"], "35", "Math", "Easy", "GPT-4"),
        
        # Math - Level 3 (Medium)
        ("M_L2_Q1", "What is 12 * 11?", ["121", "132", "144", "156"], "132", "Math", "Medium", "Claude-3"),
        ("M_L2_Q2", "What is 144 / 12?", ["10", "11", "12", "13"], "12", "Math", "Medium", "Claude-3"),
        
        # Math - Level 4 (Hard)
        ("M_L3_Q1", "What is the value of 5! (factorial)?", ["24", "60", "120", "720"], "120", "Math", "Hard", "Gemini-1.5"),
        ("M_L3_Q2", "What is the square of 25?", ["500", "625", "650", "725"], "625", "Math", "Hard", "Gemini-1.5"),

        # Math - Level 5 (Very Hard)
        ("M_L4_Q1", "Solve for x: log2(x) + log2(x-2) = 3", ["2", "4", "6", "8"], "4", "Math", "Very Hard", "Gemini-1.5"),
        ("M_L4_Q2", "What is the derivative of x^3 at x = 3?", ["9", "18", "27", "36"], "27", "Math", "Very Hard", "Gemini-1.5"),
        
        # Science - Level 1 (Very Easy)
        ("S_L0_Q1", "Which planet is known as the Red Planet?", ["Earth", "Mars", "Jupiter", "Venus"], "Mars", "Science", "Very Easy", "GPT-4"),
        ("S_L0_Q2", "What color are leaves typically?", ["Red", "Blue", "Green", "Yellow"], "Green", "Science", "Very Easy", "GPT-4"),

        # Science - Level 2 (Easy)
        ("S_L1_Q1", "Which gas do humans breathe in?", ["Oxygen", "Carbon Dioxide", "Nitrogen", "Hydrogen"], "Oxygen", "Science", "Easy", "GPT-4"),

        # Science - Level 3 (Medium)
        ("S_L2_Q1", "What is the chemical formula of water?", ["CO2", "H2O", "NaCl", "O2"], "H2O", "Science", "Medium", "Claude-3"),

        # Science - Level 4 (Hard)
        ("S_L3_Q1", "What is the speed of light in vacuum?", ["300,000 km/s", "150,000 km/s", "400,000 km/s", "30,000 km/s"], "300,000 km/s", "Science", "Hard", "Gemini-1.5"),

        # Science - Level 5 (Very Hard)
        ("S_L4_Q1", "Which subatomic particle is not found in the nucleus of a normal hydrogen atom?", ["Proton", "Neutron", "Electron", "Neutrino"], "Neutron", "Science", "Very Hard", "Gemini-1.5"),
        ("S_L4_Q2", "What is the main component of natural gas?", ["Ethane", "Propane", "Methane", "Butane"], "Methane", "Science", "Very Hard", "Gemini-1.5")
    ]
    
    query = """
    INSERT INTO questions (question_id, question, options, correct_answer, topic, level, model)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (question_id) DO UPDATE SET
        question = EXCLUDED.question,
        options = EXCLUDED.options,
        correct_answer = EXCLUDED.correct_answer,
        topic = EXCLUDED.topic,
        level = EXCLUDED.level,
        model = EXCLUDED.model;
    """
    
    for q in questions:
        cursor.execute(query, (q[0], q[1], Json(q[2]), q[3], q[4], q[5], q[6]))
        
    conn.commit()
    print("Successfully seeded questions table with 9 test questions!")
    cursor.close()
    conn.close()

if __name__ == "__main__":
    seed()
