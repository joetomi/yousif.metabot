from sqlalchemy import text, inspect
import sys

def run_upgrade(app):
    from models import db
    with app.app_context():
        engine = db.engine
        dialect_name = engine.dialect.name
        print(f"--- DATABASE MIGRATION START (Dialect: {dialect_name}) ---", flush=True)
        
        # Inspect database schema
        inspector = inspect(engine)
        try:
            table_names = inspector.get_table_names()
            print(f"Existing tables in database: {table_names}", flush=True)
        except Exception as e:
            print(f"Error inspecting table names: {e}", flush=True)
            table_names = []
        
        # Helper to add a column if it does not exist
        def add_column_if_not_exists(table, column, col_type):
            if table not in table_names:
                print(f"Table '{table}' does not exist yet. Skipping column '{column}' addition.", flush=True)
                return
                
            try:
                columns = [c['name'] for c in inspector.get_columns(table)]
                if column not in columns:
                    print(f"Adding column '{column}' to table '{table}'...", flush=True)
                    with engine.begin() as conn:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                    print(f"Successfully added column '{column}' to table '{table}'", flush=True)
                else:
                    print(f"Column '{column}' already exists in table '{table}'", flush=True)
            except Exception as e:
                err_str = str(e).lower()
                # 42701 is PostgreSQL duplicate_column error code
                if "42701" in err_str or "duplicate column" in err_str or "already exists" in err_str:
                    print(f"Column '{column}' already exists in table '{table}' (handled via exception).", flush=True)
                else:
                    print(f"Critical error adding '{column}' to table '{table}': {e}", flush=True)
                    raise e
                    
        # 1. Upgrade admins table
        add_column_if_not_exists("admins", "role", "VARCHAR(20) DEFAULT 'user'")
        add_column_if_not_exists("admins", "is_active", "BOOLEAN DEFAULT TRUE")
        add_column_if_not_exists("admins", "subscription_expires_at", "TIMESTAMP DEFAULT NULL")
        
        # 2. Upgrade settings table
        add_column_if_not_exists("settings", "user_id", "INTEGER DEFAULT NULL")
        
        # Adjust settings table unique constraints
        if "settings" in table_names:
            try:
                indexes = inspector.get_indexes("settings")
                has_composite_index = False
                for idx in indexes:
                    if idx['name'] == 'uq_setting_key_user':
                        has_composite_index = True
                    
                    # Drop old unique index on key column if present
                    if idx.get('unique') and idx.get('column_names') == ['key']:
                        idx_name = idx['name']
                        print(f"Dropping old unique index '{idx_name}' on settings(key)...", flush=True)
                        with engine.begin() as conn:
                            if dialect_name == 'postgresql':
                                try:
                                    conn.execute(text(f"ALTER TABLE settings DROP CONSTRAINT IF EXISTS {idx_name} CASCADE"))
                                except Exception:
                                    pass
                                try:
                                    conn.execute(text(f"DROP INDEX IF EXISTS {idx_name} CASCADE"))
                                except Exception:
                                    pass
                            else:
                                conn.execute(text(f"DROP INDEX IF EXISTS {idx_name}"))
                        print(f"Successfully dropped index '{idx_name}'", flush=True)
                
                if not has_composite_index:
                    print("Creating unique composite index 'uq_setting_key_user' on settings(key, user_id)...", flush=True)
                    with engine.begin() as conn:
                        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_setting_key_user ON settings(key, user_id)"))
                    print("Successfully created composite index 'uq_setting_key_user'", flush=True)
                else:
                    print("Composite index 'uq_setting_key_user' already exists.", flush=True)
            except Exception as e:
                print(f"Warning adjusting settings constraints: {e}", flush=True)
                
        # 3. Upgrade other tables
        add_column_if_not_exists("posts", "user_id", "INTEGER DEFAULT NULL")
        add_column_if_not_exists("processed_users", "admin_id", "INTEGER DEFAULT NULL")
        add_column_if_not_exists("messenger_faqs", "admin_id", "INTEGER DEFAULT NULL")
        add_column_if_not_exists("processed_messages", "admin_id", "INTEGER DEFAULT NULL")
        add_column_if_not_exists("activity_logs", "admin_id", "INTEGER DEFAULT NULL")
        
        print("--- DATABASE MIGRATION END ---", flush=True)

if __name__ == '__main__':
    from app import create_app
    app = create_app()
    run_upgrade(app)
