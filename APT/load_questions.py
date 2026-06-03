import os
import sys
import json
import argparse
import pandas as pd
import psycopg2
from psycopg2.extras import Json

def parse_options(options_raw):
    # Parses the Options cell from Excel.
    # Can handle JSON strings, comma-separated lists, semicolon-separated lists, etc.
    if pd.isna(options_raw):
        return []
    
    # If options is already a list/tuple
    if isinstance(options_raw, (list, tuple)):
        return list(options_raw)
    
    if not isinstance(options_raw, str):
        # Convert numeric/other values to string list
        return [str(options_raw).strip()]
    
    options_str = options_raw.strip()
    
    # Try parsing as JSON string
    try:
        parsed = json.loads(options_str)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed]
        return [str(parsed).strip()]
    except json.JSONDecodeError:
        pass
    
    # If not JSON, split by common delimiters
    # Try delimiters: newline, pipe, semicolon, comma
    for delimiter in ['\n', '|', ';', ',']:
        if delimiter in options_str:
            parts = [part.strip() for part in options_str.split(delimiter) if part.strip()]
            if len(parts) > 1:
                return parts
            
    # Default: single option as a list
    return [options_str]

def load_excel_to_postgres(excel_path, db_params):
    # Import questions from an Excel sheet into a PostgreSQL database.
    # 
    # Returns a dictionary of import statistics:
    # {
    #     "success": bool,
    #     "processed": int,
    #     "inserted": int,
    #     "updated": int,
    #     "error": str or None
    # }
    result = {
        "success": False,
        "processed": 0,
        "inserted": 0,
        "updated": 0,
        "error": None
    }
    
    try:
        # Load Excel sheet
        if not os.path.exists(excel_path):
            raise FileNotFoundError(f"Excel file not found at: {excel_path}")
            
        with pd.ExcelFile(excel_path) as xls:
            sheet_name = 'Questions Page'
            if sheet_name not in xls.sheet_names:
                sheet_name = xls.sheet_names[0]
            df = pd.read_excel(xls, sheet_name=sheet_name)
    except Exception as e:
        result["error"] = f"Failed to read Excel file: {str(e)}"
        return result
        
    # Map column names case-insensitively and ignoring spaces/underscores/dashes
    col_mapping = {}
    normalized_cols = {col.lower().replace(" ", "").replace("_", "").replace("-", ""): col for col in df.columns}
    
    required_cols = {
        'questionid': 'question_id',
        'question': 'question',
        'options': 'options',
        'correctanswer': 'correct_answer'
    }
    optional_cols = {
        'topic': 'topic',
        'level': 'level',
        'model': 'model'
    }
    
    for key, target in required_cols.items():
        if key in normalized_cols:
            col_mapping[normalized_cols[key]] = target
        else:
            result["error"] = f"Required column matching '{key}' not found in Excel sheet. Columns found: {list(df.columns)}"
            return result
            
    for key, target in optional_cols.items():
        if key in normalized_cols:
            col_mapping[normalized_cols[key]] = target
            
    # Rename columns to our standard format
    df_mapped = df[list(col_mapping.keys())].rename(columns=col_mapping)
    
    # Connect to database
    conn = None
    cursor = None
    try:
        conn = psycopg2.connect(**db_params)
        cursor = conn.cursor()
        
        inserted_count = 0
        updated_count = 0
        
        # Upsert query using ON CONFLICT to avoid duplicate primary key errors
        upsert_query = """
        INSERT INTO questions (question_id, question, options, correct_answer, topic, level, model)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (question_id) DO UPDATE SET
            question = EXCLUDED.question,
            options = EXCLUDED.options,
            correct_answer = EXCLUDED.correct_answer,
            topic = EXCLUDED.topic,
            level = EXCLUDED.level,
            model = EXCLUDED.model
        RETURNING (xmax = 0); -- returns true if inserted, false if updated
        """
        
        for index, row in df_mapped.iterrows():
            question_id = str(row['question_id']).strip()
            # Skip empty rows
            if not question_id or pd.isna(row['question_id']) or question_id.lower() == 'nan':
                continue
                
            question = str(row['question']).strip()
            options = parse_options(row['options'])
            correct_answer = str(row['correct_answer']).strip()
            topic = str(row['topic']).strip() if 'topic' in row and not pd.isna(row['topic']) else None
            level = str(row['level']).strip() if 'level' in row and not pd.isna(row['level']) else None
            model = str(row['model']).strip() if 'model' in row and not pd.isna(row['model']) else None
            
            # Execute upsert
            cursor.execute(upsert_query, (
                question_id,
                question,
                Json(options),
                correct_answer,
                topic,
                level,
                model
            ))
            
            is_inserted = cursor.fetchone()[0]
            if is_inserted:
                inserted_count += 1
            else:
                updated_count += 1
                
        conn.commit()
        
        result["success"] = True
        result["processed"] = len(df_mapped)
        result["inserted"] = inserted_count
        result["updated"] = updated_count
        
    except Exception as e:
        if conn:
            conn.rollback()
        result["error"] = f"Database operation failed: {str(e)}"
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
            
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load Questions Page Excel data directly into PostgreSQL (CLI Mode).")
    parser.add_argument("excel_file", help="Path to the Excel file (.xlsx or .xls)")
    parser.add_argument("--host", default="localhost", help="Database host (default: localhost)")
    parser.add_argument("--port", default="5432", help="Database port (default: 5432)")
    parser.add_argument("--dbname", default="adaptive_practice_db", help="Database name (default: adaptive_practice_db)")
    parser.add_argument("--user", default="postgres", help="Database user (default: postgres)")
    parser.add_argument("--password", default="Satya@17", help="Database password (default: Satya@17)")
    
    args = parser.parse_args()
    
    db_params = {
        "host": args.host,
        "port": args.port,
        "database": args.dbname,
        "user": args.user,
        "password": args.password
    }
    
    print(f"Running import on Excel file: {args.excel_file}...")
    res = load_excel_to_postgres(args.excel_file, db_params)
    
    if res["success"]:
        print("\nImport completed successfully!")
        print(f"Total rows processed: {res['processed']}")
        print(f"Inserted (new questions): {res['inserted']}")
        print(f"Updated (existing questions): {res['updated']}")
        sys.exit(0)
    else:
        print(f"\nImport failed: {res['error']}", file=sys.stderr)
        sys.exit(1)
