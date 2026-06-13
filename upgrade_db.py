import sqlite3
import os

def run_upgrade():
    db_path = os.path.join(os.path.dirname(__file__), 'instance', 'database.db')
    print(f"Connecting to database: {db_path}")
    
    if not os.path.exists(db_path):
        print("Database file does not exist yet. Skipping schema upgrade.")
        return
        
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
    except Exception as e:
        print(f"Failed to connect to database for upgrade: {e}")
        return
    
    def add_column(table, column, definition):
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            print(f"Successfully added column {column} to table {table}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print(f"Column {column} already exists in table {table}")
            else:
                print(f"Error adding {column} to {table}: {e}")
                
    # 1. Upgrade admins
    add_column("admins", "role", "TEXT DEFAULT 'user'")
    add_column("admins", "is_active", "INTEGER DEFAULT 1")
    add_column("admins", "subscription_expires_at", "DATETIME DEFAULT NULL")
    
    # 2. Upgrade settings
    add_column("settings", "user_id", "INTEGER DEFAULT NULL")
    
    # Drop any old single-column unique index on 'key'
    try:
        cursor.execute("PRAGMA index_list(settings)")
        indexes = cursor.fetchall()
        for idx in indexes:
            idx_name = idx[1]
            cursor.execute(f"PRAGMA index_info({idx_name})")
            cols = cursor.fetchall()
            # If it's a unique index on 'key' column only, drop it
            if idx[2] == 1 and len(cols) == 1 and cols[0][2] == 'key':
                cursor.execute(f"DROP INDEX {idx_name}")
                print(f"Dropped old unique index {idx_name} on settings(key)")
    except Exception as e:
        print(f"Could not drop index: {e}")
        
    # Create new composite unique index on settings(key, user_id)
    try:
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_setting_key_user ON settings(key, user_id)")
        print("Created unique composite index uq_setting_key_user on settings(key, user_id)")
    except Exception as e:
        print(f"Error creating composite index: {e}")
        
    # 3. Upgrade other tables
    add_column("posts", "user_id", "INTEGER DEFAULT NULL")
    add_column("processed_users", "admin_id", "INTEGER DEFAULT NULL")
    add_column("messenger_faqs", "admin_id", "INTEGER DEFAULT NULL")
    add_column("processed_messages", "admin_id", "INTEGER DEFAULT NULL")
    add_column("activity_logs", "admin_id", "INTEGER DEFAULT NULL")
    
    conn.commit()
    conn.close()
    print("Database upgrade completed successfully.")

if __name__ == '__main__':
    run_upgrade()
